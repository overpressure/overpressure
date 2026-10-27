/* Original PICO 4 KMS test. Uses advertised native modes and stock RGB layers.
 * Starts with the boot-time 90 Hz mode before switching to 72 Hz. CPU-generated
 * scanout tests display presentation independently of GPU rendering.
 * Run under an outer timeout and the POC recovery watchdog.
 */
#define _POSIX_C_SOURCE 200809L
#include <xf86drm.h>
#include <xf86drmMode.h>
#include <drm_fourcc.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

struct buffer { uint32_t handle, fb, pitch; uint64_t size; void *map; };
struct setting { uint32_t property; uint64_t value; };
struct samples {
    unsigned count, first_sequence, last_sequence, gaps, invalid;
    double first, last, first_received, last_received;
};
struct events { struct samples flips, vblanks; unsigned crtc_index; int subscribe; };
static int fd=-1;
static volatile sig_atomic_t stopped;
static void stop(int sig) { (void)sig; stopped=1; }
static void fail(const char *s) { fprintf(stderr,"%s: %s\n",s,strerror(errno)); exit(1); }
static double seconds(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec+t.tv_nsec*1e-9; }

static uint32_t optional_property(uint32_t object, uint32_t type, const char *name, uint64_t *value) {
    drmModeObjectProperties *props=drmModeObjectGetProperties(fd,object,type);
    if (!props) fail("Get object properties");
    uint32_t id=0;
    for (uint32_t i=0;i<props->count_props;i++) {
        drmModePropertyRes *p=drmModeGetProperty(fd,props->props[i]);
        if (!p) fail("Get property");
        if (!strcmp(p->name,name)) { id=p->prop_id; if(value) *value=props->prop_values[i]; }
        drmModeFreeProperty(p);
    }
    drmModeFreeObjectProperties(props);
    return id;
}
static uint32_t property(uint32_t object,uint32_t type,const char *name,uint64_t *value) {
    uint32_t id=optional_property(object,type,name,value);
    if(!id) {errno=ENOENT;fail(name);}return id;
}
static void set(drmModeAtomicReq *req,uint32_t obj,uint32_t type,const char *name,uint64_t value) {
    if(drmModeAtomicAddProperty(req,obj,property(obj,type,name,NULL),value)<0) fail("Add property");
}
static uint64_t enum_value(uint32_t object,const char *name,const char *option) {
    drmModePropertyRes *p=drmModeGetProperty(fd,property(object,DRM_MODE_OBJECT_PLANE,name,NULL));
    if(!p)fail("Enum property");
    for(int i=0;i<p->count_enums;i++) if(!strcmp(p->enums[i].name,option)) {
        uint64_t value=p->enums[i].value;drmModeFreeProperty(p);return value;
    }
    drmModeFreeProperty(p);errno=EINVAL;fail(option);return 0;
}

static void inspect_planes(void) {
    drmModePlaneRes *planes=drmModeGetPlaneResources(fd);if(!planes)fail("Inspect planes");
    for(uint32_t i=0;i<planes->count_planes;i++) {
        uint32_t id=planes->planes[i];
        drmModeObjectProperties *props=drmModeObjectGetProperties(fd,id,DRM_MODE_OBJECT_PLANE);
        if(!props)fail("Inspect properties");
        printf("plane %u:",id);
        for(uint32_t j=0;j<props->count_props;j++) {
            drmModePropertyRes *p=drmModeGetProperty(fd,props->props[j]);if(!p)fail("Inspect property");
            if(!(p->flags&DRM_MODE_PROP_BLOB)) printf(" %s=%llu",p->name,(unsigned long long)props->prop_values[j]);
            drmModeFreeProperty(p);
        }
        puts("");drmModeFreeObjectProperties(props);
    }
    drmModeFreePlaneResources(planes);
}

static struct buffer allocate(int eye,int combined) {
    unsigned width=combined?4320:2160;
    struct drm_mode_create_dumb create={.width=width,.height=2160,.bpp=32};
    if(drmIoctl(fd,DRM_IOCTL_MODE_CREATE_DUMB,&create)) fail("Create dumb buffer");
    struct buffer b={.handle=create.handle,.pitch=create.pitch,.size=create.size};
    uint32_t handles[4]={b.handle}, pitches[4]={b.pitch}, offsets[4]={0};
    if(drmModeAddFB2(fd,width,2160,DRM_FORMAT_XRGB8888,handles,pitches,offsets,&b.fb,0)) fail("Add framebuffer");
    struct drm_mode_map_dumb mapping={.handle=b.handle};
    if(drmIoctl(fd,DRM_IOCTL_MODE_MAP_DUMB,&mapping)) fail("Map dumb buffer");
    b.map=mmap(NULL,b.size,PROT_READ|PROT_WRITE,MAP_SHARED,fd,(off_t)mapping.offset);
    if(b.map==MAP_FAILED) fail("mmap framebuffer");
    for(unsigned y=0;y<2160;y++) for(unsigned column=0;column<width;column++) {
        unsigned x=column%2160;
        if(combined)eye=column/2160;
        uint32_t color=eye?0x00102040:0x00402010;
        if(x%180<4 || y%180<4) color=0x00606060;
        if(x<8 || x>=2152 || y<8 || y>=2152) color=0x00a0a0a0;
        /* L on the left half, R on the right half, centered for lens visibility. */
        unsigned dx=x>=850?x-850:9999, dy=y>=800?y-800:9999;
        int letter=eye ? (dx<55 && dy<560) || (dy<55 && dx<350) || (dy>=245 && dy<300 && dx<350) ||
            (dx>=295 && dx<350 && dy<300) || (dy>=280 && dy<560 && abs((int)dx-(int)(dy-280))<40)
            : (dx<55 && dy<560) || (dy>=505 && dy<560 && dx<350);
        if(letter) color=0x00c0c0c0;
        *(uint32_t *)((char *)b.map+y*b.pitch+column*4)=color;
    }
    return b;
}

static void sample(struct samples *s,unsigned sequence,unsigned sec,unsigned usec) {
    double t=sec+usec*1e-6, received=seconds();
    if(!s->count) { s->first=t; s->first_sequence=sequence; s->first_received=received; }
    else if(t<=s->last || sequence==s->last_sequence) s->invalid++;
    else if((unsigned)(sequence-s->last_sequence)!=1) s->gaps++;
    if(t==0) s->invalid++;
    s->last=t; s->last_sequence=sequence; s->last_received=received; s->count++;
}
static void subscribe_vblank(struct events *e) {
    drmVBlank request={0};
    request.request.type=DRM_VBLANK_RELATIVE|DRM_VBLANK_EVENT|
        (e->crtc_index<<DRM_VBLANK_HIGH_CRTC_SHIFT);
    request.request.sequence=1;
    request.request.signal=(unsigned long)e;
    if(drmWaitVBlank(fd,&request)) fail("Subscribe to CRTC vblank");
}
static void flip(int device,unsigned sequence,unsigned sec,unsigned usec,void *data) {
    (void)device; struct events *e=data; sample(&e->flips,sequence,sec,usec);
}
static void vblank(int device,unsigned sequence,unsigned sec,unsigned usec,void *data) {
    (void)device; struct events *e=data; sample(&e->vblanks,sequence,sec,usec);
    if(e->subscribe && !stopped) subscribe_vblank(e);
}
static double rate(const struct samples *s) {
    return s->count>1 && !s->invalid && s->last>s->first ?
        (unsigned)(s->last_sequence-s->first_sequence)/(s->last-s->first) : 0;
}
static void nullable_rate(double hz) { if(hz>0) printf("%.4f",hz); else printf("null"); }
static void nullable_gaps(const struct samples *s) {
    if(s->count>1 && !s->invalid) printf("%u",s->gaps); else printf("null");
}
static void wait_event(struct events *e,int static_frame) {
    struct samples *wanted=static_frame?&e->vblanks:&e->flips;
    unsigned before=wanted->count; double deadline=seconds()+3;
    drmEventContext ctx={.version=2,.page_flip_handler=flip,.vblank_handler=vblank};
    while(wanted->count==before && !stopped) {
        struct pollfd p={.fd=fd,.events=POLLIN};
        int ret=poll(&p,1,500);
        if(ret<0 && errno==EINTR) continue;
        if(ret<0 || p.revents&(POLLERR|POLLHUP)) fail("DRM event poll");
        if(ret>0 && drmHandleEvent(fd,&ctx)) fail("DRM event read");
        if(seconds()>deadline) { errno=ETIMEDOUT; fail("No DRM completion"); }
    }
}

static void wait_retired(int fence) {
    double deadline=seconds()+3;
    drmEventContext ctx={.version=2,.page_flip_handler=flip,.vblank_handler=vblank};
    while(!stopped) {
        struct pollfd polls[2]={{.fd=fence,.events=POLLIN},{.fd=fd,.events=POLLIN}};
        int ret=poll(polls,2,500);
        if(ret<0 && errno==EINTR)continue;
        if(ret<0 || (polls[0].revents|polls[1].revents)&(POLLERR|POLLNVAL))fail("Retire fence poll");
        if(polls[1].revents&POLLIN)if(drmHandleEvent(fd,&ctx))fail("Fence wait DRM events");
        if(polls[0].revents&POLLIN)return;
        if(seconds()>deadline){errno=ETIMEDOUT;fail("Retire fence timeout");}
    }
}

int main(int argc,char **argv) {
    int duration=10,inspect=argc==2 && !strcmp(argv[1],"--inspect");
    int stock_layers=1,static_frame=0,repeat_buffer=0,blocking=0,initial_buffer=0,full_state=0,combined=1,retire=1;
    if(argc>=2 && !inspect) {
        char *end;long n=strtol(argv[1],&end,10);if(*end || n<3 || n>120)return 2;duration=(int)n;
        for(int i=2;i<argc;i++) {
            if(!strcmp(argv[i],"--stock-layers"))stock_layers=1;
            else if(!strcmp(argv[i],"--opaque-layers"))stock_layers=0;
            else if(!strcmp(argv[i],"--static"))static_frame=1;
            else if(!strcmp(argv[i],"--static-buffer-one")) {static_frame=1;initial_buffer=1;}
            else if(!strcmp(argv[i],"--repeat-buffer"))repeat_buffer=1;
            else if(!strcmp(argv[i],"--blocking"))blocking=1;
            else if(!strcmp(argv[i],"--full-state"))full_state=1;
            else if(!strcmp(argv[i],"--combined-buffer"))combined=1;
            else if(!strcmp(argv[i],"--per-eye-buffers"))combined=0;
            else if(!strcmp(argv[i],"--retire-fence"))retire=1;
            else if(!strcmp(argv[i],"--no-retire-fence"))retire=0;
            else return 2;
        }
    }
    if(!inspect && access("/etc/pico4-poc/build-info",R_OK)) { errno=EPERM; fail("POC environment required"); }
    signal(SIGINT,stop);signal(SIGTERM,stop);
    fd=open("/dev/dri/card0",O_RDWR|O_CLOEXEC); if(fd<0) fail("Open DRM");
    drmVersion *version=drmGetVersion(fd);
    if(!version || strcmp(version->name,"msm_drm")) { errno=ENODEV;fail("Expected stock msm_drm"); }
    drmFreeVersion(version);
    if(drmSetClientCap(fd,DRM_CLIENT_CAP_UNIVERSAL_PLANES,1) || drmSetClientCap(fd,DRM_CLIENT_CAP_ATOMIC,1)) fail("Enable DRM properties");
    if(inspect) { inspect_planes();close(fd);return 0; }
    if(drmSetMaster(fd)) fail("Acquire atomic DRM master");
    drmModeRes *res=drmModeGetResources(fd); if(!res) fail("DRM resources");
    drmModeConnector *conn=NULL;
    for(int i=0;i<res->count_connectors;i++) {
        drmModeConnector *c=drmModeGetConnector(fd,res->connectors[i]);
        if(c && c->connector_type==DRM_MODE_CONNECTOR_DSI && c->connection==DRM_MODE_CONNECTED) {
            if(conn) { errno=EINVAL;fail("Ambiguous DSI connector"); } conn=c;
        } else if(c) drmModeFreeConnector(c);
    }
    if(!conn) { errno=ENODEV;fail("Connected DSI absent"); }
    drmModeModeInfo modes[2]; int found[2]={0}; const int hz[2]={90,72};
    for(int m=0;m<conn->count_modes;m++) for(int i=0;i<2;i++) {
        char expected[32];snprintf(expected,sizeof(expected),"4320x2160x%dx",hz[i]);
        if(conn->modes[m].hdisplay==4320 && conn->modes[m].vdisplay==2160 && !strncmp(conn->modes[m].name,expected,strlen(expected))) {
            if(found[i]++) {errno=EINVAL;fail("Ambiguous native mode");} modes[i]=conn->modes[m];
        }
    }
    if(!found[0] || !found[1]) {errno=ENODEV;fail("Native 90/72 Hz modes absent");}
    uint32_t possible=0;
    for(int i=0;i<conn->count_encoders;i++) {drmModeEncoder *e=drmModeGetEncoder(fd,conn->encoders[i]);if(e){possible|=e->possible_crtcs;drmModeFreeEncoder(e);}}
    int crtc_index=0;while(crtc_index<res->count_crtcs && !(possible&(1u<<crtc_index))) crtc_index++;
    if(crtc_index==res->count_crtcs) {errno=ENODEV;fail("No compatible CRTC");}
    uint32_t crtc=res->crtcs[crtc_index],planes[6]={0}; unsigned np=0,nvig=0,ndma=0;
    uint32_t retire_property=retire?property(conn->connector_id,DRM_MODE_OBJECT_CONNECTOR,"RETIRE_FENCE",NULL):0;
    uint32_t release_property=retire?property(crtc,DRM_MODE_OBJECT_CRTC,"output_fence",NULL):0;
    unsigned required=stock_layers?6:2;
    const int stock_eye[6]={1,0,1,0,1,0},stock_stage[6]={3,2,1,0,5,4};
    const char *stock_color[6]={"blue","blue","red","red","green","green"};
    drmModePlaneRes *pr=drmModeGetPlaneResources(fd);if(!pr)fail("Planes");
    for(uint32_t i=0;i<pr->count_planes && np<required;i++) {
        drmModePlane *p=drmModeGetPlane(fd,pr->planes[i]); if(!p)continue;
        uint64_t type;property(p->plane_id,DRM_MODE_OBJECT_PLANE,"type",&type);
        int rgb=0;for(uint32_t j=0;j<p->count_formats;j++) if(p->formats[j]==DRM_FORMAT_XRGB8888)rgb=1;
        if(rgb && type!=DRM_PLANE_TYPE_CURSOR && p->possible_crtcs&(1u<<crtc_index)) {
            int scaler=optional_property(p->plane_id,DRM_MODE_OBJECT_PLANE,"scaler_v2",NULL)!=0;
            int multirect=optional_property(p->plane_id,DRM_MODE_OBJECT_PLANE,"multirect_mode",NULL)!=0;
            if(!stock_layers) planes[np++]=p->plane_id;
            else if(scaler && nvig<4) { planes[nvig++]=p->plane_id;np++; }
            else if(!scaler && !multirect && type==DRM_PLANE_TYPE_OVERLAY && ndma<2) {
                planes[4+ndma++]=p->plane_id;np++;
            }
        }
        drmModeFreePlane(p);
    }
    drmModeFreePlaneResources(pr);if(np!=required){errno=ENODEV;fail("Required RGB planes absent");}
    uint32_t fb_properties[6];
    for(unsigned i=0;i<required;i++)fb_properties[i]=property(planes[i],DRM_MODE_OBJECT_PLANE,"FB_ID",NULL);
    const char *state_names[]={"CRTC_ID","sspp_layout","layer_color_component","blend_op","rotation","zpos",
        "CRTC_X","CRTC_Y","CRTC_W","CRTC_H","SRC_X","SRC_Y","SRC_W","SRC_H"};
    enum { state_count=14 };
    struct setting plane_state[6][state_count];
    for(unsigned i=0;i<required;i++) {
        int e=stock_layers?stock_eye[i]:(int)i;uint32_t p=planes[i];
        uint64_t values[]={crtc,enum_value(p,"sspp_layout",e?"right":"left"),
            enum_value(p,"layer_color_component",stock_layers?stock_color[i]:"none"),
            enum_value(p,"blend_op",stock_layers?"layer_color":"opaque"),DRM_MODE_ROTATE_0,
            stock_layers?stock_stage[i]:e,0,0,2160,2160,combined?(unsigned)e*2160u<<16:0,0,2160u<<16,2160u<<16};
        for(unsigned j=0;j<state_count;j++)plane_state[i][j]=(struct setting){
            property(p,DRM_MODE_OBJECT_PLANE,state_names[j],NULL),values[j]};
    }
    struct buffer buffers[2][2];for(int e=0;e<(combined?1:2);e++)for(int b=0;b<2;b++)buffers[e][b]=allocate(e,combined);
    int success=1;
    /* Keep callback storage alive until the DRM fd closes: a queued vblank can
     * arrive while disabling the CRTC or starting the next mode. */
    struct events events[2]={0};
    int diagnostic=static_frame||repeat_buffer||!stock_layers||!combined||!retire;
    printf("{\"schema\":2,\"connector\":%u,\"crtc\":%u,\"width\":4320,\"height\":2160,\"eye_routing\":\"sspp_layout\",\"composition\":\"%s\",\"presentation\":\"%s\",\"commit_mode\":\"%s\",\"initial_buffer\":%d,\"full_plane_state\":%s,\"buffer_layout\":\"%s\",\"retire_fence\":%s,\"modes\":[",conn->connector_id,crtc,stock_layers?"rgb_layers":"opaque",static_frame?"static":repeat_buffer?"repeat_buffer":"page_flip",blocking?"blocking":"nonblocking",initial_buffer,full_state?"true":"false",combined?"combined":"per_eye",retire?"true":"false");fflush(stdout);
    for(int m=0;m<2 && !stopped;m++) {
        uint32_t blob;if(drmModeCreatePropertyBlob(fd,&modes[m],sizeof(modes[m]),&blob))fail("Mode blob");
        drmModeAtomicReq *req=drmModeAtomicAlloc();if(!req)fail("Atomic request");
        set(req,conn->connector_id,DRM_MODE_OBJECT_CONNECTOR,"CRTC_ID",crtc);
        set(req,crtc,DRM_MODE_OBJECT_CRTC,"MODE_ID",blob);set(req,crtc,DRM_MODE_OBJECT_CRTC,"ACTIVE",1);
        for(unsigned i=0;i<required;i++) {
            int e=stock_layers?stock_eye[i]:(int)i;
            uint32_t p=planes[i];
            if(drmModeAtomicAddProperty(req,p,fb_properties[i],buffers[combined?0:e][initial_buffer].fb)<0)fail("Initial framebuffer property");
            /* Stock PICO routes eyes by sspp_layout, with eye-local positions.
             * Its 4320-wide mode is not a generic side-by-side CRTC layout. */
            for(unsigned j=0;j<state_count;j++)
                if(drmModeAtomicAddProperty(req,p,plane_state[i][j].property,plane_state[i][j].value)<0)fail("Initial plane property");
        }
        if(drmModeAtomicCommit(fd,req,DRM_MODE_ATOMIC_TEST_ONLY|DRM_MODE_ATOMIC_ALLOW_MODESET,NULL))fail("Test native mode");
        if(drmModeAtomicCommit(fd,req,DRM_MODE_ATOMIC_ALLOW_MODESET,NULL))fail("Commit native mode");
        drmModeAtomicFree(req);drmModeDestroyPropertyBlob(fd,blob);
        fprintf(stderr,"Native %d Hz pattern active for %d seconds\n",hz[m],duration);
        struct events *ev=&events[m];ev->crtc_index=(unsigned)crtc_index;ev->subscribe=1;
        /* This vendor driver does not acquire a vblank reference for page-flip
         * completions. Request vblank explicitly throughout the measurement. */
        subscribe_vblank(ev);
        double start=seconds();unsigned frame=0;
        while(!stopped && seconds()-start<duration) {
            if(static_frame) {wait_event(ev,1);continue;}
            req=drmModeAtomicAlloc();if(!req)fail("Flip request");
            ++frame;
            for(unsigned i=0;i<required;i++) {
                int e=stock_layers?stock_eye[i]:(int)i;
                if(drmModeAtomicAddProperty(req,planes[i],fb_properties[i],buffers[combined?0:e][repeat_buffer?0:frame&1].fb)<0)fail("Flip framebuffer property");
                if(full_state)for(unsigned j=0;j<state_count;j++)
                    if(drmModeAtomicAddProperty(req,planes[i],plane_state[i][j].property,plane_state[i][j].value)<0)fail("Repeated plane property");
            }
            uint32_t flags=DRM_MODE_PAGE_FLIP_EVENT|(blocking?0:DRM_MODE_ATOMIC_NONBLOCK);
            uint64_t release_fd=UINT64_MAX,retire_fd=UINT64_MAX;
            if(retire) {
                /* Follow the stock display userspace fence contract. Page-flip
                 * events alone were measured at the right rate but produced
                 * visible corruption on this kernel. These vendor properties
                 * return 64-bit fd values through userspace pointers. */
                if(drmModeAtomicAddProperty(req,crtc,release_property,(uint64_t)(uintptr_t)&release_fd)<0 ||
                   drmModeAtomicAddProperty(req,conn->connector_id,retire_property,(uint64_t)(uintptr_t)&retire_fd)<0)
                    fail("Request display fences");
            }
            if(drmModeAtomicCommit(fd,req,flags,ev))fail("Page flip");
            drmModeAtomicFree(req);wait_event(ev,0);
            if(retire) {
                if(release_fd>INT_MAX || retire_fd>INT_MAX){errno=EBADF;fail("Missing display fence");}
                wait_retired((int)retire_fd);close((int)retire_fd);close((int)release_fd);
            }
        }
        ev->subscribe=0;
        double elapsed=ev->flips.last_received-ev->flips.first_received;
        double completed=ev->flips.count>1 && elapsed>0?(ev->flips.count-1)/elapsed:0;
        double measured=rate(&ev->vblanks);
        int ok=!stopped && ev->vblanks.count>=(unsigned)(duration*hz[m]*0.9) &&
            (static_frame || (ev->flips.count>=(unsigned)(duration*hz[m]*0.9) &&
             completed>hz[m]*0.95 && completed<hz[m]*1.05)) &&
            measured>hz[m]*0.95 && measured<hz[m]*1.05 && !ev->vblanks.gaps;
        success&=ok;
        printf("%s{\"requested_hz\":%d,\"completion_hz\":%.4f,\"flips\":%u,\"page_flip_hz\":",m?",":"",hz[m],completed,ev->flips.count);
        nullable_rate(rate(&ev->flips));printf(",\"page_flip_sequence_gaps\":");nullable_gaps(&ev->flips);
        printf(",\"vblank_hz\":");nullable_rate(measured);
        printf(",\"vblank_samples\":%u,\"vblank_sequence_gaps\":",ev->vblanks.count);nullable_gaps(&ev->vblanks);
        printf(",\"status\":\"%s\"}",ok?(diagnostic?"diagnostic":"passed"):"failed");fflush(stdout);
    }
    drmModeAtomicReq *off=drmModeAtomicAlloc();if(!off)fail("Disable request");
    for(unsigned i=0;i<required;i++){set(off,planes[i],DRM_MODE_OBJECT_PLANE,"FB_ID",0);set(off,planes[i],DRM_MODE_OBJECT_PLANE,"CRTC_ID",0);}
    set(off,conn->connector_id,DRM_MODE_OBJECT_CONNECTOR,"CRTC_ID",0);
    set(off,crtc,DRM_MODE_OBJECT_CRTC,"MODE_ID",0);set(off,crtc,DRM_MODE_OBJECT_CRTC,"ACTIVE",0);
    if(drmModeAtomicCommit(fd,off,DRM_MODE_ATOMIC_ALLOW_MODESET,NULL))fail("Disable display");
    drmModeAtomicFree(off);
    for(int e=0;e<(combined?1:2);e++)for(int b=0;b<2;b++) {struct buffer *p=&buffers[e][b];drmModeRmFB(fd,p->fb);munmap(p->map,p->size);struct drm_mode_destroy_dumb destroy={.handle=p->handle};drmIoctl(fd,DRM_IOCTL_MODE_DESTROY_DUMB,&destroy);}
    drmModeFreeConnector(conn);drmModeFreeResources(res);drmDropMaster(fd);close(fd);
    printf("],\"status\":\"%s\"}\n",success&&!stopped?(diagnostic?"diagnostic":"passed"):"failed");return success&&!stopped?0:1;
}
