// Copyright 2026, overpressure contributors.
// SPDX-License-Identifier: BSL-1.0
/*
 * pico4-onboarding: the first run's introduction in the headset (docs/session.md#onboarding).
 *
 * The Steam client's own first-run setup runs here as on any SteamOS device, but the Frame's
 * guided tour does not (device type 541). This takes its place for what the PICO 4 needs:
 *
 * - Controllers. With no controller connected 20 s after the start, the laser cannot reach
 *   anything, the client's setup included: the panel comes up in front of the wearer and the
 *   station listens for one controller after the other, through the driver's pairing socket
 *   (pico4_station.h), while the panel says what to press. With one controller connected, its
 *   laser pairs the other from the panel.
 * - After the client's setup, once: health and safety, the laser and the PICO button, the
 *   playspace. Skipping or finishing it leaves its mark in ~/.local/share/pico4/onboarding-done;
 *   the dashboard's Introduction tab shows it again.
 *
 *   pico4-onboarding [--render DIR]
 *
 * --render draws every page into DIR as PNG files, without SteamVR.
 */
#include "pico4_onboarding.h"
#include "pico4_overlay.hpp"

#include <errno.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>

#include <cstdlib>
#include <fstream>
#include <sstream>

namespace {

using namespace pico4_overlay;

volatile sig_atomic_t g_stop;

void
stop_requested(int)
{
	g_stop = 1;
}

std::string
home_path(const char *rest)
{
	const char *home = getenv("HOME");
	return std::string(home != nullptr ? home : "/tmp") + rest;
}

bool
done_mark()
{
	return access(home_path("/.local/share/pico4/onboarding-done").c_str(), F_OK) == 0;
}

void
set_done_mark()
{
	mkdir(home_path("/.local").c_str(), 0755);
	mkdir(home_path("/.local/share").c_str(), 0755);
	mkdir(home_path("/.local/share/pico4").c_str(), 0755);
	std::ofstream(home_path("/.local/share/pico4/onboarding-done")) << "1\n";
}

bool
oobe_done()
{
	std::ifstream in(home_path("/.steam/registry.vdf"));
	std::stringstream text;
	text << in.rdbuf();
	return pico4_tour_oobe_done(text.str().c_str());
}

//! One request to the driver's pairing socket, read without waiting.
struct Request
{
	int fd = -1;
	std::vector<std::string> answers;
	bool done = false;

	//! Connect and send; false when the driver has no socket (no session, or it is starting).
	bool start(const std::string &text)
	{
		const char *runtime = getenv("XDG_RUNTIME_DIR");
		sockaddr_un addr{};
		addr.sun_family = AF_UNIX;
		snprintf(addr.sun_path, sizeof(addr.sun_path), "%s/%s", runtime != nullptr ? runtime : "/run/user/1000",
		         "pico4-station");
		// Without waiting: a driver busy with another request leaves the connection in its backlog.
		fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
		if (fd < 0 || connect(fd, reinterpret_cast<const sockaddr *>(&addr), sizeof(addr)) != 0 ||
		    send(fd, text.data(), text.size(), MSG_NOSIGNAL) != (ssize_t)text.size()) {
			finish();
			return false;
		}
		done = false;
		answers.clear();
		return true;
	}

	//! Take what arrived; true when something did.
	bool poll()
	{
		bool any = false;
		char buf[256];
		while (fd >= 0) {
			ssize_t n = recv(fd, buf, sizeof(buf) - 1, MSG_DONTWAIT);
			if (n > 0) {
				buf[n] = '\0';
				answers.emplace_back(buf);
				any = true;
			} else if (n == 0 || (errno != EAGAIN && errno != EWOULDBLOCK)) {
				finish();
				done = true;
				any = true;
			} else {
				break;
			}
		}
		return any;
	}

	void finish()
	{
		if (fd >= 0) {
			close(fd);
		}
		fd = -1;
	}

	bool active() const { return fd >= 0; }
};

struct State
{
	enum pico4_tour_page page = PICO4_TOUR_CONTROLLERS;
	bool shown = false;     //!< The panel in front of the wearer.
	bool in_tab = false;    //!< The pages run in the dashboard's tab (started from it).
	bool tour = false;      //!< The pages past the controllers follow.
	bool known = false;     //!< The driver answered a status.
	bool left = false, right = false;
	int pairing = 0;        //!< The hand being paired: 1 left, 2 right.
	int last_hands_free = 0;
	bool hands_free = false; //!< Pairing started because no controller was there.
	std::string message;
	int pressed = PICO4_TOUR_NONE;
};

const char *
hand_name(int hand)
{
	return hand == 1 ? "left" : "right";
}

void
button(cairo_t *c, double x, const char *label, bool pressed, bool primary)
{
	rounded(c, x, PICO4_TOUR_BUTTON_Y, PICO4_TOUR_BUTTON_W, PICO4_TOUR_BUTTON_H, 16);
	if (pressed) {
		cairo_set_source_rgb(c, 0.20, 0.44, 0.72);
	} else if (primary) {
		cairo_set_source_rgb(c, 0.16, 0.36, 0.60);
	} else {
		cairo_set_source_rgb(c, 0.22, 0.24, 0.27);
	}
	cairo_fill(c);
	cairo_set_source_rgb(c, 1, 1, 1);
	text_centered(c, label, x + PICO4_TOUR_BUTTON_W / 2, PICO4_TOUR_BUTTON_Y + 52, 30, CAIRO_FONT_WEIGHT_BOLD);
}

//! @p text broken into lines of at most @p width at @p size, drawn from @p y down; returns the next y.
double
paragraph(cairo_t *c, const char *text, double y, double size, double width)
{
	cairo_select_font_face(c, kFont, CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_NORMAL);
	cairo_set_font_size(c, size);
	std::string line;
	for (const std::string &word : split_words(text)) {
		std::string longer = line.empty() ? word : line + " " + word;
		cairo_text_extents_t e;
		cairo_text_extents(c, longer.c_str(), &e);
		if (e.width > width && !line.empty()) {
			cairo_move_to(c, 60, y);
			cairo_show_text(c, line.c_str());
			y += size * 1.4;
			line = word;
		} else {
			line = longer;
		}
	}
	if (!line.empty()) {
		cairo_move_to(c, 60, y);
		cairo_show_text(c, line.c_str());
		y += size * 1.4;
	}
	return y + size * 0.4;
}

void
draw(cairo_surface_t *surface, const State &s)
{
	cairo_t *c = cairo_create(surface);
	cairo_set_source_rgb(c, 0.11, 0.12, 0.14);
	cairo_paint(c);
	const struct pico4_tour_text &t = pico4_tour_texts[s.page];
	cairo_set_source_rgb(c, 1, 1, 1);
	cairo_select_font_face(c, kFont, CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_BOLD);
	cairo_set_font_size(c, 48);
	cairo_move_to(c, 60, 100);
	cairo_show_text(c, t.title);

	double y = 170;
	const double width = PICO4_TOUR_W - 120;
	const int pair_hand = pico4_tour_pair_button(s.left, s.right);
	if (s.page == PICO4_TOUR_CONTROLLERS) {
		char line[128];
		for (int hand = 1; hand <= 2; hand++) {
			const bool on = hand == 1 ? s.left : s.right;
			snprintf(line, sizeof(line), "%s controller: %s", hand == 1 ? "Left" : "Right",
			         !s.known ? "..." : on ? "connected" : s.pairing == hand ? "waiting for it" : "not connected");
			cairo_set_source_rgb(c, on ? 0.55 : 0.95, on ? 0.85 : 0.75, on ? 0.55 : 0.45);
			y = paragraph(c, line, y, 32, width);
		}
		y += 10;
		cairo_set_source_rgb(c, 0.85, 0.87, 0.90);
		if (s.pairing != 0) {
			snprintf(line, sizeof(line), "To pair the %s controller:", hand_name(s.pairing));
			y = paragraph(c, line, y, 30, width);
			y = paragraph(c, PICO4_TOUR_PAIR_HOW, y, 30, width);
		} else if (s.left && s.right) {
			y = paragraph(c, t.lines[0], y, 30, width);
		} else if (pair_hand != 0) {
			snprintf(line, sizeof(line), "Point the other controller at Pair to pair the %s one.", hand_name(pair_hand));
			y = paragraph(c, line, y, 30, width);
		}
	} else {
		cairo_set_source_rgb(c, 0.85, 0.87, 0.90);
		for (int l = 0; l < PICO4_TOUR_LINES && t.lines[l] != nullptr; l++) {
			y = paragraph(c, t.lines[l], y, 30, width);
		}
	}
	if (!s.message.empty()) {
		cairo_set_source_rgb(c, 0.95, 0.55, 0.45);
		paragraph(c, s.message.c_str(), 440, 26, width);
	}
	const bool last = s.page == PICO4_TOUR_DONE || (!s.tour && s.page == PICO4_TOUR_CONTROLLERS);
	button(c, PICO4_TOUR_SKIP_X, s.tour ? "Skip" : "Close", s.pressed == PICO4_TOUR_SKIP, false);
	if (s.page == PICO4_TOUR_CONTROLLERS && pair_hand != 0 && s.pairing == 0) {
		button(c, PICO4_TOUR_PAIR_X, "Pair", s.pressed == PICO4_TOUR_PAIR, false);
	}
	button(c, PICO4_TOUR_NEXT_X, last ? "Done" : "Next", s.pressed == PICO4_TOUR_NEXT, true);
	cairo_destroy(c);
	cairo_surface_flush(surface);
}

void
draw_start(cairo_surface_t *surface, bool pressed)
{
	cairo_t *c = cairo_create(surface);
	cairo_set_source_rgb(c, 0.11, 0.12, 0.14);
	cairo_paint(c);
	cairo_set_source_rgb(c, 1, 1, 1);
	text_centered(c, "Introduction", PICO4_TOUR_W / 2.0, 140, 48, CAIRO_FONT_WEIGHT_BOLD);
	cairo_set_source_rgb(c, 0.85, 0.87, 0.90);
	text_centered(c, "Controllers, safety, the PICO button and your playspace.", PICO4_TOUR_W / 2.0, 230, 28,
	              CAIRO_FONT_WEIGHT_NORMAL);
	button(c, PICO4_TOUR_NEXT_X - 330, "Show it", pressed, true);
	cairo_destroy(c);
	cairo_surface_flush(surface);
}

void
draw_thumbnail(cairo_surface_t *surface)
{
	cairo_t *c = cairo_create(surface);
	cairo_set_source_rgb(c, 0.11, 0.12, 0.14);
	cairo_paint(c);
	cairo_set_source_rgb(c, 1, 1, 1);
	text_centered(c, "?", 128, 178, 150, CAIRO_FONT_WEIGHT_BOLD);
	cairo_destroy(c);
	cairo_surface_flush(surface);
}

//! The panel 1.5 m in front of the wearer, level, facing them, where they look now.
void
place_in_front(vr::IVROverlay *overlay, vr::IVRSystem *system, vr::VROverlayHandle_t handle)
{
	vr::TrackedDevicePose_t pose;
	system->GetDeviceToAbsoluteTrackingPose(vr::TrackingUniverseStanding, 0, &pose, 1);
	if (!pose.bPoseIsValid) {
		// Head-locked until the head is tracked.
		vr::HmdMatrix34_t m = {{{1, 0, 0, 0}, {0, 1, 0, 0}, {0, 0, 1, -1.5f}}};
		overlay->SetOverlayTransformTrackedDeviceRelative(handle, vr::k_unTrackedDeviceIndex_Hmd, &m);
		return;
	}
	const vr::HmdMatrix34_t &h = pose.mDeviceToAbsoluteTracking;
	double fx = -h.m[0][2], fz = -h.m[2][2]; // the head's forward, level
	const double n = std::sqrt(fx * fx + fz * fz);
	if (n < 1e-3) {
		fx = 0, fz = -1;
	} else {
		fx /= n, fz /= n;
	}
	// The panel's +z (its front) points back at the wearer: a turn about up by atan2(-fx, -fz).
	const double yaw = std::atan2(-fx, -fz), cy = std::cos(yaw), sy = std::sin(yaw);
	vr::HmdMatrix34_t m = {{{(float)cy, 0, (float)sy, (float)(h.m[0][3] + 1.5 * fx)},
	                        {0, 1, 0, h.m[1][3]},
	                        {(float)-sy, 0, (float)cy, (float)(h.m[2][3] + 1.5 * fz)}}};
	overlay->SetOverlayTransformAbsolute(handle, vr::TrackingUniverseStanding, &m);
}

int
render(const char *dir)
{
	cairo_surface_t *surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, PICO4_TOUR_W, PICO4_TOUR_H);
	State s;
	s.known = true;
	s.tour = true;
	int r = 0;
	const struct
	{
		const char *name;
		enum pico4_tour_page page;
		bool left, right;
		int pairing;
	} pages[] = {
	    {"controllers-none", PICO4_TOUR_CONTROLLERS, false, false, 1},
	    {"controllers-one", PICO4_TOUR_CONTROLLERS, true, false, 0},
	    {"controllers-both", PICO4_TOUR_CONTROLLERS, true, true, 0},
	    {"safety", PICO4_TOUR_SAFETY, true, true, 0},
	    {"controls", PICO4_TOUR_CONTROLS, true, true, 0},
	    {"playspace", PICO4_TOUR_PLAYSPACE, true, true, 0},
	    {"done", PICO4_TOUR_DONE, true, true, 0},
	};
	for (const auto &p : pages) {
		s.page = p.page;
		s.left = p.left;
		s.right = p.right;
		s.pairing = p.pairing;
		draw(surface, s);
		const std::string file = std::string(dir) + "/" + p.name + ".png";
		r |= cairo_surface_write_to_png(surface, file.c_str()) != CAIRO_STATUS_SUCCESS;
	}
	draw_start(surface, false);
	r |= cairo_surface_write_to_png(surface, (std::string(dir) + "/start.png").c_str()) != CAIRO_STATUS_SUCCESS;
	cairo_surface_destroy(surface);
	return r;
}

} // namespace

int
main(int argc, char **argv)
{
	if (argc == 3 && strcmp(argv[1], "--render") == 0) {
		return render(argv[2]);
	}
	signal(SIGPIPE, SIG_IGN);
	signal(SIGTERM, stop_requested);
	signal(SIGINT, stop_requested);

	Vr vrs;
	const int opened = vr_open("pico4-onboarding", vrs, &g_stop);
	if (opened != 0) {
		return opened == 2 ? 0 : 1;
	}
	vr::IVROverlay *overlay = vrs.overlay;
	vr::IVRSystem *system = vrs.system;

	vr::VROverlayHandle_t front, tab, thumbnail;
	if (overlay->CreateOverlay("pico4.onboarding", "PICO 4 introduction", &front) != vr::VROverlayError_None ||
	    overlay->CreateDashboardOverlay("pico4.onboarding.tab", "Introduction", &tab, &thumbnail) !=
	        vr::VROverlayError_None) {
		fprintf(stderr, "pico4-onboarding: no overlays\n");
		return 1;
	}
	vr::HmdVector2_t scale = {{(float)PICO4_TOUR_W, (float)PICO4_TOUR_H}};
	for (vr::VROverlayHandle_t h : {front, tab}) {
		overlay->SetOverlayInputMethod(h, vr::VROverlayInputMethod_Mouse);
		overlay->SetOverlayMouseScale(h, &scale);
	}
	overlay->SetOverlayWidthInMeters(front, 1.2f);
	overlay->SetOverlayWidthInMeters(tab, 2.0f);
	// The laser reaches the panel outside the dashboard too.
	overlay->SetOverlayFlag(front, vr::VROverlayFlags_MakeOverlaysInteractiveIfVisible, true);

	cairo_surface_t *surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, PICO4_TOUR_W, PICO4_TOUR_H);
	Gpu gpu;
	Texture front_texture, tab_texture, thumbnail_texture;
	if (!gpu_create(gpu, vrs.compositor, "pico4-onboarding") || !texture_create(gpu, front_texture, PICO4_TOUR_W, PICO4_TOUR_H) ||
	    !texture_create(gpu, tab_texture, PICO4_TOUR_W, PICO4_TOUR_H) || !texture_create(gpu, thumbnail_texture, 256, 256)) {
		fprintf(stderr, "pico4-onboarding: no Vulkan textures\n");
		return 1;
	}
	{
		cairo_surface_t *small = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 256, 256);
		draw_thumbnail(small);
		texture_show(gpu, thumbnail_texture, small, overlay, thumbnail);
		cairo_surface_destroy(small);
		draw_start(surface, false);
		texture_show(gpu, tab_texture, surface, overlay, tab);
	}
	printf("pico4-onboarding: ready\n");
	fflush(stdout);

	State s;
	Request status, pair;
	const double started = now_s();
	double next_status = 0, next_oobe = 0;
	bool oobe = false, mark = done_mark(), dirty = true, running = true, tab_pressed = false;

	auto show = [&](bool tour) {
		s.tour = tour;
		s.page = PICO4_TOUR_CONTROLLERS;
		s.message.clear();
		if (!s.shown) {
			place_in_front(overlay, system, front);
			overlay->ShowOverlay(front);
			s.shown = true;
		}
		dirty = true;
	};
	auto hide = [&](bool finished) {
		if (finished && s.tour) {
			set_done_mark();
			mark = true;
		}
		if (s.in_tab) {
			s.in_tab = false;
			draw_start(surface, false);
			texture_show(gpu, tab_texture, surface, overlay, tab);
		} else {
			overlay->HideOverlay(front);
			s.shown = false;
		}
		s.tour = false;
	};
	auto start_pairing = [&](int hand) {
		char text[32];
		snprintf(text, sizeof(text), "pair %s %d", hand_name(hand), PICO4_TOUR_PAIR_S);
		if (pair.start(text)) {
			s.pairing = hand;
			s.message.clear();
		} else {
			s.message = "The controller radio is not ready yet.";
		}
		dirty = true;
	};

	while (running && !g_stop) {
		const double now = now_s();
		vr::VREvent_t e;
		for (vr::VROverlayHandle_t h : {front, tab}) {
			while (overlay->PollNextOverlayEvent(h, &e, sizeof(e))) {
				const double x = e.data.mouse.x, y = PICO4_TOUR_H - e.data.mouse.y; // SteamVR's y points up
				const bool pair_shown = s.page == PICO4_TOUR_CONTROLLERS && s.pairing == 0 &&
				                        pico4_tour_pair_button(s.left, s.right) != 0;
				// The tab's own start page; once started there, the pages run in the tab (OpenVR has no
				// call that closes the dashboard, which would cover the panel in front).
				if (h == tab && !s.in_tab) {
					const bool on = x >= PICO4_TOUR_NEXT_X - 330 && x < PICO4_TOUR_NEXT_X - 330 + PICO4_TOUR_BUTTON_W &&
					                y >= PICO4_TOUR_BUTTON_Y && y < PICO4_TOUR_BUTTON_Y + PICO4_TOUR_BUTTON_H;
					if (e.eventType == vr::VREvent_MouseButtonDown && on) {
						tab_pressed = true;
					} else if (e.eventType == vr::VREvent_MouseButtonUp && tab_pressed) {
						tab_pressed = false;
						if (on && !s.shown) {
							s.in_tab = true;
							s.tour = true;
							s.page = PICO4_TOUR_CONTROLLERS;
							s.message.clear();
							dirty = true;
						}
					}
					continue;
				}
				if ((h == tab) != s.in_tab) {
					continue;
				}
				if (e.eventType == vr::VREvent_MouseButtonDown) {
					s.pressed = pico4_tour_hit(x, y, pair_shown);
					dirty = true;
				} else if (e.eventType == vr::VREvent_MouseButtonUp) {
					const int was = s.pressed;
					s.pressed = PICO4_TOUR_NONE;
					dirty = true;
					if (was != pico4_tour_hit(x, y, pair_shown)) {
						continue;
					}
					if (was == PICO4_TOUR_SKIP) {
						hide(true);
					} else if (was == PICO4_TOUR_PAIR) {
						s.hands_free = false;
						start_pairing(pico4_tour_pair_button(s.left, s.right));
					} else if (was == PICO4_TOUR_NEXT) {
						const enum pico4_tour_page next = pico4_tour_next(s.page);
						if (!s.tour || next == PICO4_TOUR_PAGES) {
							hide(true);
						} else {
							s.page = next;
						}
					}
				}
			}
		}
		while (system->PollNextEvent(&e, sizeof(e))) {
			if (e.eventType == vr::VREvent_Quit) {
				system->AcknowledgeQuit_Exiting();
				running = false;
			}
		}

		// The controllers' state from the driver, while no pairing holds its socket.
		if (!pair.active() && !status.active() && now >= next_status) {
			next_status = now + 2;
			status.start("status");
		}
		if (status.poll() && status.done) {
			bool left, right;
			if (!status.answers.empty() && pico4_tour_parse_status(status.answers[0].c_str(), &left, &right)) {
				dirty = dirty || !s.known || left != s.left || right != s.right;
				s.known = true;
				s.left = left;
				s.right = right;
			}
			status.answers.clear();
		}
		if (pair.poll() && pair.done) {
			const int result = pair.answers.empty() ? -1 : pico4_tour_pair_answer(pair.answers.back().c_str());
			if (result != 1 && !s.hands_free) {
				s.message = "No controller paired. Try again with Pair.";
			}
			s.pairing = 0;
			next_status = 0; // the new state, now
			dirty = true;
		}
		// Hands-free pairing: nothing points at the panel while no controller is there.
		if (s.known && !s.left && !s.right && s.pairing == 0 && !s.in_tab && now - started >= PICO4_TOUR_CONTROLLER_WAIT_S) {
			if (!s.shown) {
				show(s.tour);
			}
			s.hands_free = true;
			s.last_hands_free = pico4_tour_hands_free(false, false, s.last_hands_free);
			start_pairing(s.last_hands_free);
		}
		if (now >= next_oobe) {
			next_oobe = now + 5;
			oobe = oobe_done();
		}
		if (s.known && (s.left || s.right) && !s.in_tab && pico4_tour_starts(oobe, mark)) {
			if (!s.shown) {
				show(true);
			} else if (!s.tour) {
				s.tour = true; // the controllers came hands-free: the tour goes on from here
				dirty = true;
			}
		}
		if (dirty && (s.shown || s.in_tab)) {
			draw(surface, s);
			if (!texture_show(gpu, s.in_tab ? tab_texture : front_texture, surface, overlay, s.in_tab ? tab : front)) {
				fprintf(stderr, "pico4-onboarding: image not accepted\n");
			}
			dirty = false;
		}
		usleep(s.shown || s.in_tab ? 16000 : 200000);
	}
	pair.finish();
	status.finish();
	cairo_surface_destroy(surface);
	overlay->DestroyOverlay(front);
	overlay->DestroyOverlay(tab);
	vrs.shutdown();
	return 0;
}
