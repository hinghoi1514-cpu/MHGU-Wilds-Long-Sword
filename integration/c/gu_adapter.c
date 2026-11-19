#include "moveset.h"
#if ML_WORLD_COMBAT
#include "world_core.h"
#endif
typedef uint32_t u32;
typedef uintptr_t uptr;
typedef u32 (*NativeCall)(void *,u32,u32,u32);
typedef uptr (__attribute__((pcs("aapcs-vfp"))) *NativeLayer)(void *,u32,u32,u32,float,float,float);
typedef struct {void *address;u32 length;const void *bytes,*original;unsigned char enabled,pad[3];} PublicPatch;
typedef struct {void *target,*detour,*original,*context;void **cell;unsigned char has_context;} PublicHook;
_Static_assert(sizeof(PublicPatch)==20,"Forge ARM32 Patch ABI");
_Static_assert(sizeof(PublicHook)==24,"Forge ARM32 Hook ABI");
extern PublicPatch forge_patch_create(void *,const void *,u32,u32);
extern void forge_patch_enable(PublicPatch *);
extern void forge_patch_disable(PublicPatch *);
extern void forge_patch_destroy(PublicPatch *);
extern PublicHook forge_hook_create(void *,void *,void **);

enum {VT=0x174cf64,STATE=0x2506,ACTION=0x250a,HEADING=0xfec,TARGET=0x14a0,
      FRAME=0x500,MOTION=0x4b4,AREA=0x1054,HP=0x13d6,DRAWN=0x2749,AIR=0x2514};
static uptr main_base;
static NativeCall original_weapon,original_common1,original_common2,original_request,virtual_request;
static NativeLayer original_layer;
#if ML_ANIMATION_PREVIEW || ML_WORLD_COMBAT
static PublicPatch patches[6];
static NativeCall original_equipment;
#else
static PublicPatch patches[4];
#endif
static u32 patch_count,installed,submitting,update_depth,held,sample_sequence,pad_valid;
static u32 bind_generation;static void *bind_actor;
static MLState controller;
static u32 logs,seen_actor;
static u32 seen_native=0xffffffffu,seen_motion=0xffffffffu;
static uint16_t u16(const void *p,u32 off){return *(const uint16_t *)((uptr)p+off);}
static u32 word(const void *p,u32 off){return *(const u32 *)((uptr)p+off);}
static float real(const void *p,u32 off){return *(const float *)((uptr)p+off);}
static void put(void *p,u32 off,u32 v){*(u32 *)((uptr)p+off)=v;}
static unsigned byte(const void *p,u32 off){return *(const unsigned char *)((uptr)p+off);}

static unsigned world_resource_path(const char *a,const char *b)
{
    for(unsigned i=0;i<64;++i){char c=a[i]=='\\'?'/':a[i];if(c!=b[i])return 0;if(!c)return 1;}
    return 0;
}
static void log_event(const char *name,u32 a,u32 x,u32 y,u32 z)
{
    if(logs>=12000)return;
    ++logs;
    char b[160];u32 n=0;const char *prefix="ML_REBUILD: ",*hex="0123456789ABCDEF";
    for(u32 i=0;prefix[i];++i)b[n++]=prefix[i];
    for(u32 i=0;name[i]&&n<100;++i)b[n++]=name[i];
    u32 values[]={a,x,y,z};
    for(u32 j=0;j<4;++j){b[n++]=' ';for(int bit=28;bit>=0;bit-=4)b[n++]=hex[(values[j]>>bit)&15];}
    b[n++]='\n';
    register const char *r0 __asm__("r0")=b;register u32 r1 __asm__("r1")=n;
    __asm__ volatile("svc #0x27":"+r"(r0),"+r"(r1)::"memory","cc");
}
static void ml_pad_read(void)
{
    if(!installed)return;
    held=0;pad_valid=0;
    uptr pad=*(uptr *)(main_base+0x2126888);if(!pad||(pad&3))return;
    int index=*(int *)(pad+0x6b60);if(index<0||index>8)return;
    u32 raw=*(u32 *)(pad+(u32)index*0xb50+0x1d0);
    held=((raw&0x1000)?ML_X:0)|((raw&0x2000)?ML_A:0)|((raw&0x800)?ML_R:0)|
         ((raw&0x4000)?ML_B:0)|((raw&0x8000)?ML_Y:0)|((raw&0x200)?ML_ZR:0);
    pad_valid=1;
}
void ml_pad_refresh(void){++sample_sequence;ml_pad_read();}
u32 ml_pad_raw_bits(void)
{
    uptr pad=*(uptr *)(main_base+0x2126888);
    if(!pad||(pad&3))return 0;
    int index=*(int *)(pad+0x6b60);
    if(index<0||index>8)return 0;
    return *(u32 *)(pad+(u32)index*0xb50+0x1d0);
}
static unsigned hunter_style(void *a)
{
    return ((unsigned (*)(void *))(main_base+0x268818))(a);
}
static unsigned valor_active(void *a)
{
    return hunter_style(a)==5&&(word(a,10056)&0x400000u);
}
static unsigned local_supported(void *a)
{
    if(!a||word(a,0)!=main_base+VT||!(word(a,0x13cc)&1))return 0;
#if ML_TEST_ANY_STYLE
    /* test builds: the automation save is not a Guild-style hunter */
    return 1;
#else
    unsigned style=hunter_style(a);
    return style==0 || (ML_WILDS_MOVES && style==5);
#endif
}
#if ML_ANIMATION_PREVIEW || ML_WORLD_COMBAT
#if ML_ATTACHMENT_PREVIEW || ML_WORLD_COMBAT
#include "attachment_data.h"
#if ML_WILDS_MOVES
#include "wilds_root_data.h"
#include "focus_walk_data.h"
static const WildsClip *focus_walk_clip(unsigned motion)
{
    for(unsigned i=0;i<sizeof(focus_walk_clips)/sizeof(*focus_walk_clips);++i)
        if(motion==0x500+focus_walk_clips[i].slot)return focus_walk_clips+i;
    return 0;
}
#endif
static NativeCall original_placement,original_equipment_motion;
/* Native3078b8 only rewrites the local pose when its placement-table pointer
 * changes, and writes nothing when that pointer is null. Preserve its input
 * state across our temporary source attachment, including the cache pointer.
 * Otherwise source312 frame34 reparents the last hand offset onto the back. */
static struct {u32 object,owner,active,pose[12],previous,joint,kind,cache;} attachment_native[2];
static unsigned attachment_pose(void *a,u32 role,float *out,u32 *joint,u32 *closed)
{
    u32 motion=u16(a,MOTION);float frame=real(a,FRAME);
    if((motion>>8)!=5||!(frame>=0&&frame<10000))return 0;
#if ML_WILDS_MOVES
    const WildsClip *wc=0;
    for(unsigned i=0;i<sizeof(wilds_clips)/sizeof(*wilds_clips);++i)
        if(wilds_clips[i].slot==motion-0x500){wc=wilds_clips+i;break;}
    const float (*weapon_samples)[7]=wilds_weapon_samples;
    if(!wc){wc=focus_walk_clip(motion);weapon_samples=focus_walk_weapon_samples;}
    if(role==9&&wc){
        /* Once HunterBasic turns the weapon OFF, native placement owns the
         * closed blade on its scabbard. Do not reattach it to the hand. */
        if((wc->source==917&&frame>=150)||(wc->source==904&&frame>=140))return 0;
        unsigned n=(unsigned)frame;if(n>=wc->frames)n=wc->frames-1;
        unsigned next=n+1<wc->frames?n+1:n;float t=frame-(float)n;if(t>1)t=1;
        const float *x=weapon_samples[wc->offset+n],*y=weapon_samples[wc->offset+next];
        for(unsigned i=0;i<3;++i)out[i]=x[i]+(y[i]-x[i])*t;out[3]=0;
        float dot=0,length=0;for(unsigned i=3;i<7;++i)dot+=x[i]*y[i];
        for(unsigned i=0;i<4;++i){out[4+i]=x[3+i]+((dot<0?-1:1)*y[3+i]-x[3+i])*t;length+=out[4+i]*out[4+i];}
        float norm;__asm__("vsqrt.f32 %0, %1":"=t"(norm):"t"(length));
        if(!(norm>.00001f))return 0;for(unsigned i=0;i<4;++i)out[4+i]/=norm;
        out[8]=out[9]=out[10]=1;out[11]=0;*joint=12;*closed=0;return 1;
    }
#endif
    const MLAttachmentClip *clip=0;
    for(u32 i=0;i<sizeof(ml_attachment_clips)/sizeof(*clip);++i)
        if(ml_attachment_clips[i].slot==motion-0x500){clip=&ml_attachment_clips[i];break;}
    if(!clip)return 0;
    u32 n=(u32)frame;if(n>=clip->frames)n=clip->frames-1;
    *closed=ml_attachment_weapon_motion[clip->offset+n]==0;
    u32 selected=ml_attachment_selection[clip->offset+n][role-9];
    if(!selected)return 0;
    u32 next=n+1<clip->frames?n+1:n;
    const float *x=ml_attachment_samples[clip->offset+n]+(selected-1)*7;
    const float *y=ml_attachment_samples[clip->offset+next]+(selected-1)*7;
    float frac=frame-(float)n;if(frac>1)frac=1;
    for(u32 i=0;i<3;++i)out[i]=x[i]+(y[i]-x[i])*frac;
    out[3]=0;float dot=0;
    for(u32 i=3;i<7;++i)dot+=x[i]*y[i];
    float sign=dot<0?-1:1,len2=0,len;
    for(u32 i=3;i<7;++i){out[i+1]=x[i]+(sign*y[i]-x[i])*frac;len2+=out[i+1]*out[i+1];}
    __asm__("vsqrt.f32 %0, %1":"=t"(len):"t"(len2));
    if(!(len>0.00001f))return 0;
    for(u32 i=4;i<8;++i)out[i]/=len;
    out[8]=out[9]=out[10]=1;out[11]=0;*joint=selected==1?8:12;
    return 1;
}
/* During 312 the authored hand selector disappears at frame 34. Preserve
 * the evaluated equipment transform when crossing to the native back joint.
 * Work in the new joint's local space so turning cannot leave the sword behind. */
static struct {void *object,*owner;u32 epoch;float pose[7],last;} holster[2];
static float att_sqrt(float v){float x;__asm__("vsqrt.f32 %0, %1":"=t"(x):"t"(v));return x;}
/* The scabbard retains the weapon model's actual back placement while
 * drawn. The private timeout has no GU placement-table row of its own. */
static struct {void *owner;unsigned epoch,model;float pose[12];} back_rest[2];
static unsigned holster_pose(void *a,void *o,unsigned ix,unsigned prior,float *out,u32 *joint,u32 *closed)
{
    float f=real(a,FRAME);
    unsigned motion=u16(a,MOTION),round=0;
#if ML_WILDS_MOVES
    round=ix==0&&motion==0x500+219;
#endif
    float start=round?136:34,finish=round?150:68;
    if((!round&&motion!=0x5f6)||!(f>=start&&f<(round?176:90))){holster[ix].object=0;return 0;}
    unsigned rest_ix=round?0:1;
    unsigned rest=back_rest[rest_ix].owner==a&&back_rest[rest_ix].epoch==byte(a,AREA)&&
        back_rest[rest_ix].model==word(a,round?0x23a0:0x23a4);
    if(round&&!rest){
        rest_ix=1;
        rest=back_rest[1].owner==a&&back_rest[1].epoch==byte(a,AREA)&&back_rest[1].model==word(a,0x23a4);
    }
    *joint=round?2:word(o,0x13cc);*closed=1;
    if(*joint>=255)return 0;
    if(!holster[ix].object && prior && f<start+5){
        const float *p=((const float *(*)(void *,u32))(*(uptr **)a)[0x54/4])(a,*joint);
        const float *m=(const float *)((uptr)o+0xb0);
        if(!p)return 0;
        float rot[9],*v=holster[ix].pose;
        for(unsigned i=0;i<3;++i){
            float x=p[i*4],y=p[i*4+1],z=p[i*4+2],nn=x*x+y*y+z*z;
            if(!(nn>.01f&&nn<100))return 0;
            v[i]=(x*(m[12]-p[12])+y*(m[13]-p[13])+z*(m[14]-p[14]))/nn;
            for(unsigned j=0;j<3;++j)rot[i*3+j]=(x*m[j*4]+y*m[j*4+1]+z*m[j*4+2])/nn;
        }
        float *q=v+3,diag=rot[0]+rot[4]+rot[8];
        if(diag>0){float t=2*att_sqrt(1+diag);q[3]=t*.25f;q[0]=(rot[7]-rot[5])/t;q[1]=(rot[2]-rot[6])/t;q[2]=(rot[3]-rot[1])/t;}
        else {unsigned i=rot[0]>rot[4]?0:1;if(rot[8]>rot[i*3+i])i=2;unsigned j=(i+1)%3,k=(i+2)%3;
            float t=2*att_sqrt(1+rot[3*i+i]-rot[3*j+j]-rot[3*k+k]);if(!(t>.01f))return 0;
            q[i]=t*.25f;q[j]=(rot[3*i+j]+rot[3*j+i])/t;q[k]=(rot[3*i+k]+rot[3*k+i])/t;q[3]=(rot[3*k+j]-rot[3*j+k])/t;}
        holster[ix].object=o;holster[ix].owner=a;holster[ix].epoch=byte(a,AREA);holster[ix].last=f;
    }
    unsigned active=holster[ix].object==o&&holster[ix].owner==a&&holster[ix].epoch==byte(a,AREA);
    if(round&&f>=finish&&rest){
        for(unsigned i=0;i<12;++i)out[i]=back_rest[rest_ix].pose[i];
        return 1;
    }
    if(!active||holster[ix].last>f|| (round&&!rest))return 0;
    holster[ix].last=f;
    float t=(f-start)/(finish-start);if(t>1)t=1;t=t*t*(3-2*t);float *v=holster[ix].pose;
    for(unsigned i=0;i<12;++i)out[i]=rest?back_rest[rest_ix].pose[i]:real(o,0x40+i*4);
    for(unsigned i=0;i<3;++i)out[i]=v[i]+t*(out[i]-v[i]);
    float dot=0,n=0;for(unsigned i=0;i<4;++i)dot+=v[3+i]*out[4+i];
    for(unsigned i=0;i<4;++i){out[4+i]=v[3+i]*(1-t)+(dot<0?-t:t)*out[4+i];n+=out[4+i]*out[4+i];}
    if(!(n>.001f))return 0;n=att_sqrt(n);for(unsigned i=0;i<4;++i)out[4+i]/=n;
    return 1;
}
static u32 preview_placement(void *object,u32 owner,u32 role,u32 flags)
{
    unsigned ix=role==10?1:0;
    unsigned prior=attachment_native[ix].active&&attachment_native[ix].object==(u32)(uptr)object&&attachment_native[ix].owner==owner;
    if((role==9||role==10)&&attachment_native[ix].active){
        if(attachment_native[ix].object==(u32)(uptr)object&&attachment_native[ix].owner==owner){
            for(unsigned i=0;i<12;++i)put(object,0x40+4*i,attachment_native[ix].pose[i]);
            put(object,0x13c8,attachment_native[ix].previous);
            put(object,0x13cc,attachment_native[ix].joint);
            put(object,0x13d4,attachment_native[ix].kind);
            put(object,0x1408,attachment_native[ix].cache);
        }
        attachment_native[ix].active=0;
    }
    u32 result=original_placement(object,owner,role,flags);
    void *a=(void *)(uptr)owner;float pose[12];u32 joint,closed;
    if((role!=9&&role!=10)||!local_supported(a))return result;
    unsigned motion=u16(a,MOTION),private_pose=0;
#if ML_WILDS_MOVES
    private_pose=focus_walk_clip(motion)!=0;
    for(unsigned i=0;!private_pose&&i<sizeof(wilds_clips)/sizeof(*wilds_clips);++i)
        private_pose=motion==0x500+wilds_clips[i].slot;
#endif
    if(!private_pose&&motion!=0x500+219&&(role==10||!(byte(a,DRAWN)&1))&&
       word(object,0x13cc)==2&&word(object,0x13d4)==2&&word(object,0x1408)&&
       (motion>>8!=5||motion<0x5e0)){
        back_rest[ix].owner=a;back_rest[ix].epoch=byte(a,AREA);back_rest[ix].model=(u32)(uptr)object;
        for(unsigned i=0;i<12;++i)back_rest[ix].pose[i]=real(object,0x40+4*i);
    }
    unsigned wilds_back=private_pose&&role==10&&back_rest[1].owner==a&&
        back_rest[1].epoch==byte(a,AREA)&&back_rest[1].model==(u32)(uptr)object;
    if(wilds_back){
        for(unsigned j=0;j<12;++j)pose[j]=back_rest[1].pose[j];
        joint=2;closed=1;
    }
    unsigned round_holster=0;
#if ML_WILDS_MOVES
    round_holster=role==9&&u16(a,MOTION)==0x500+219&&real(a,FRAME)>=136&&
        holster_pose(a,object,ix,prior,pose,&joint,&closed);
#endif
    if(!round_holster){
        if(wilds_back||attachment_pose(a,role,pose,&joint,&closed))holster[ix].object=0;
        else if(!holster_pose(a,object,ix,prior,pose,&joint,&closed))return result;
    }
    attachment_native[ix].object=(u32)(uptr)object;attachment_native[ix].owner=owner;
    for(unsigned i=0;i<12;++i)attachment_native[ix].pose[i]=word(object,0x40+4*i);
    attachment_native[ix].previous=word(object,0x13c8);
    attachment_native[ix].joint=word(object,0x13cc);
    attachment_native[ix].kind=word(object,0x13d4);
    attachment_native[ix].cache=word(object,0x1408);attachment_native[ix].active=1;
    for(u32 i=0;i<12;++i)*(float *)((uptr)object+0x40+4*i)=pose[i];
    put(object,0x13c8,word(object,0x13cc));put(object,0x13cc,joint);
    put(object,0x13d4,role==10||closed?2:1);put(object,0x1408,0);
    ((void (*)(void *,void *,u32))(*(uptr **)object)[0x48/4])(object,a,joint);
    /* The outer native update still owns weapon animation, visibility,
     * matrix evaluation and its base update. Never run those twice. */
    return result;
}
static u32 preview_equipment_motion(void *object,u32 owner,u32 c,u32 d)
{
    void *a=(void *)(uptr)owner;
    if(word(object,0x13d0)==9&&local_supported(a)){
        unsigned motion=u16(a,MOTION);float frame=real(a,FRAME);
#if ML_WILDS_MOVES
        unsigned wilds=0;
        for(unsigned i=0;i<sizeof(wilds_clips)/sizeof(*wilds_clips);++i)
            if(motion==0x500+wilds_clips[i].slot){wilds=1;break;}
        if(wilds||focus_walk_clip(motion)){
            unsigned closed=(motion==0x500+219&&frame>=150)||(motion==0x500+250&&frame>=140);
            if(u16(object,0x5f4)!=0xffffu)
                ((NativeLayer)(main_base+0x954640))(object,1,0xffffu,0,0,0,1);
            if(u16(object,MOTION)!=(closed?0:1))
                ((NativeLayer)(main_base+0x954640))(object,0,closed?0:1,0,0,0,1);
            put(object,0x13c4,5000u+motion-0x500);return 0;
        }
#endif
        if((motion>>8)==5&&frame>=0&&frame<10000)
        for(unsigned i=0;i<sizeof(ml_attachment_clips)/sizeof(*ml_attachment_clips);++i){
            const MLAttachmentClip *clip=ml_attachment_clips+i;
            if(clip->slot!=motion-0x500)continue;
            unsigned n=(unsigned)frame;if(n>=clip->frames)n=clip->frames-1;
            int source=ml_attachment_weapon_motion[clip->offset+n];
            if(source<0||source>3)break;
            unsigned target=source<2?(unsigned)source:220u+(unsigned)source-2u;
            if(!((u32 (*)(void *,u32))(main_base+0x953b10))(object,target))break;
            /* Native fallback keeps a second layer101 (drawn pose). Leaving
             * it active overrides our blade scale even when layer0 advances.
             * Match native308d10's missing-secondary handling for private clips;
             * ordinary body motion will restore its own secondary layer. */
            if(u16(object,0x5f4)!=0xffffu)
                ((NativeLayer)(main_base+0x954640))(object,1,0xffffu,0,0,0,1);
            unsigned body=5000u+clip->slot;
            if(source>=2||u16(object,MOTION)!=target||word(object,0x13c4)!=body){
                float age=source<2?0:(float)ml_attachment_weapon_age[clip->offset+n]+frame-(float)n;
                if(age>10)age=10;
                if(u16(object,MOTION)!=target||word(object,0x13c4)!=body)
                    ((NativeLayer)(main_base+0x954640))(object,0,target,0,0,age,1);
                /* Native308bf4 synchronizes a weapon by writing requested
                 * frame4fc and setting4bc bit4, retaining normal rate1.
                 * Source306[73,80) and307..311[0,8) have body/weapon rate1. */
                if(source>=2){
                    *(float *)((uptr)object+0x4fc)=age;
                    put(object,0x4bc,word(object,0x4bc)|0x10u);
                }
            }
            put(object,0x13c4,body);
            return 0;
        }
    }
    return original_equipment_motion(object,owner,c,d);
}
#endif
static u32 preview_equipment(void *object,u32 b,u32 c,u32 d)
{
    void *a=(void *)(uptr)word(object,0x30);u32 role=word(object,0x13d0);
    if((role!=9&&role!=10)||!local_supported(a))return original_equipment(object,b,c,d);
    u32 result=original_equipment(object,b,c,d);
#if ML_WORLD_COMBAT
    return result; /* Combat builds do not need per-bone preview telemetry. */
#else
    static void *objects[2];static u32 motions[2],bins[2];
    u32 ix=role-9,motion=u16(a,MOTION);float frame=real(a,FRAME);
    if((motion>>8)!=5||!(frame>=0&&frame<10000))return result;
    if(motion==0x501&&frame>=1)return result;
    u32 bin=(u32)frame/32;
    if(objects[ix]==object&&motions[ix]==motion&&bins[ix]==bin)return result;
    objects[ix]=object;motions[ix]=motion;bins[ix]=bin;
    log_event("equip_state",(u32)(uptr)object,role,motion,word(object,0x13cc));
    log_event("equip_clock",(u32)(uptr)object,word(a,FRAME),word(object,0x13d4),(u32)(uptr)a);
    log_event("equip_motion",(u32)(uptr)object,word(object,0x4b4),word(object,0x500),word(object,0x110));
    for(u32 i=0;i<4;++i)
        log_event("equip_pose",(u32)(uptr)object,word(object,0x40+i*12),word(object,0x44+i*12),word(object,0x48+i*12));
    if(motion==0x5e6&&bin==3){
        const void *parent=((const void *(*)(void *,u32))(*(uptr **)a)[0x54/4])(a,word(object,0x13cc));
        if(parent)for(u32 i=0;i<4;++i)
            log_event("equip_parent",(u32)(uptr)object,word(parent,i*16),word(parent,i*16+4),word(parent,i*16+8));
        log_event("equip_actor",(u32)(uptr)object,word(a,0x40),word(a,0x44),word(a,0x48));
    }
    return result;
#endif
}
#endif
static MLObservation observe(void *a,unsigned direction)
{
    MLObservation o={0};o.actor=(u32)(uptr)a;
    o.epoch=byte(a,AREA);o.sample_sequence=sample_sequence;
    o.bind_generation=a==bind_actor?bind_generation:0;
    o.native_action=u16(a,ACTION);o.motion=u16(a,MOTION);o.phase=byte(a,5);
    o.held=held;o.gauge=word(a,0x2780);o.heading=u16(a,HEADING);o.input_heading=o.heading;
    o.animation_frame=real(a,FRAME);o.simulation_delta=real(a,0x1c);
    /* Native thrust f_139a5fc calls f_b50bcc(actor+0x4b0): byte6 bit2. */
    o.motion_finished=(byte(a,0x4b6)>>2)&1;
    o.magnitude=(unsigned)(int)(int16_t)u16(a,0x1410);
    if(o.magnitude>128)o.magnitude=0;
    o.eligible=pad_valid&&u16(a,STATE)==16&&(byte(a,DRAWN)&1)&&
        (int16_t)u16(a,HP)>0&&!(word(a,AIR)&0x400);
    if(direction&&o.magnitude>=50){
        u32 target=word(a,TARGET);
        ((void (*)(void *))(main_base+0x27f678))(a);
        o.input_heading=u16(a,TARGET);put(a,TARGET,target);
    }
    return o;
}
#if ML_WORLD_COMBAT
#include "world_adapter.inc"
#endif
static u32 request_hook(void *a,u32 state,u32 action,u32 flags)
{
    if(submitting||!local_supported(a))return original_request(a,state,action,flags);
    /* Requests can precede the post-poll hook. Refresh bits without adding
     * another simulation sample or advancing any buffered-key timer. */
    ml_pad_read();
#if ML_WORLD_COMBAT
    if(world_request(a,state,action,flags))return 0;
#if ML_WORLD_INP
