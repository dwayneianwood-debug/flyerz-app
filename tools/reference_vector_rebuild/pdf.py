import json, math, sys, os, numpy as np, cv2
from PIL import Image, ImageCms
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import mm
sys.path.insert(0,'/workspace/medwork')
import bg
Image.MAX_IMAGE_PIXELS=None
OUT='/workspace/medella_vector'; W='/workspace/medwork'
PT=72/25.4
D='/workspace/medfonts/static/'
_reg={}
WN={400:'Regular',500:'Medium',600:'SemiBold',700:'Bold'}
def press_path(path):
    b=os.path.basename(path); P='/workspace/medfonts/press/'
    import re
    m=re.match(r'Cinzel-w(\d+)\.ttf',b)
    if m: return P+f'Cinzel-{WN[int(m.group(1))]}.ttf'
    m=re.match(r'EBGaramond-Italic-w(\d+)\.ttf',b)
    if m: return P+f'EBGaramond-{WN[int(m.group(1))]}Italic.ttf'
    m=re.match(r'EBGaramond-w(\d+)\.ttf',b)
    if m: return P+f'EBGaramond-{WN[int(m.group(1))]}.ttf'
    return P+b
def font(path):
    path=press_path(path)
    n=os.path.basename(path).replace('.ttf','').replace('-','_')
    if n not in _reg:
        pdfmetrics.registerFont(TTFont(n,path)); _reg[n]=path
    return n
_srgb=ImageCms.createProfile('sRGB')
_cmyk=ImageCms.getOpenProfile('/usr/share/color/icc/ghostscript/default_cmyk.icc')
_tr=ImageCms.buildTransform(_srgb,_cmyk,'RGB','CMYK',renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,flags=ImageCms.Flags.BLACKPOINTCOMPENSATION)
def cmyk(rgb,cls=''):
    r,g,b=[int(round(x)) for x in rgb]
    if min(r,g,b)>=245: return (0,0,0,0)
    if cls.startswith('neutral'):
        L=(0.2126*r+0.7152*g+0.0722*b)/255
        if L<0.35: return (0,0,0,1.0)   # dark neutral text: solid 100% K (matches near-black original)
        return (0,0,0,round(min(1,1-L)*1.0,3))
    px=Image.new('RGB',(1,1),(r,g,b)); c=ImageCms.applyTransform(px,_tr).getpixel((0,0))
    return tuple(round(v/255,3) for v in c)
GOLD=(186,126,38)
SIDE={'flyer_front':dict(page=(158,220),s=205/1536,off=((158-1024*205/1536)/2,7.5)),
      'flyer_back':dict(page=(158,220),s=148/1054,off=(5,(220-1492*148/1054)/2)),
      'card_front':dict(page=(100,60),s=50/992,off=((100-1586*50/992)/2,5))}
def set_boxes(c,pw,ph,bleed=5):
    c.setPageSize((pw*mm,ph*mm))
    c.setTrimBox((bleed*mm,bleed*mm,(pw-bleed)*mm,(ph-bleed)*mm))
    c.setBleedBox((0,0,pw*mm,ph*mm))
    c.setArtBox((bleed*mm,bleed*mm,(pw-bleed)*mm,(ph-bleed)*mm))
def draw_text(c,ph,x_mm,y_mm,text,fpath,size_pt,cs_pt=0,hs=1.0,rgb=(0,0,0),cls='',sw_pt=0,angle=0):
    col=cmyk(rgb,cls)
    c.saveState()
    c.translate(x_mm*mm,(ph-y_mm)*mm)
    if angle: c.rotate(-angle)
    t=c.beginText(0,0)
    t.setFont(font(fpath),size_pt)
    t.setCharSpace(cs_pt); t.setHorizScale(hs*100)
    c.setFillColorCMYK(*col)
    if sw_pt>0:
        c.setStrokeColorCMYK(*col); c.setLineWidth(sw_pt); c.setLineJoin(1); t.setTextRenderMode(2)
    else: t.setTextRenderMode(0)
    t.textOut(text)
    c.drawText(t); c.restoreState()
def bg_image(c,path,pw,ph):
    c.drawImage(path,0,0,pw*mm,ph*mm)
COLOR_OVERRIDE={('flyer_front','green'):(8,30,20),('card_front','green'):(8,32,12)}
WEIGHT_OVERRIDE={'flyer_front:17':D+'EBGaramond-Italic-w400.ttf'}
HEAD_SWAP={'flyer_back':(D+'EBGaramond-w600.ttf',lambda r:r['text'].isupper() and r['cclass']=='navy_dark' and 8<=int(r['id'].split(':')[1])<=63)}
def head_swap(L,np_,sel,cc=0.70,ce=0.65):
    # condition headings: Garamond-style bold caps (narrow '&', normal word spaces), one size for all, same ink width as original
    R=[r for r in L if sel(r)]
    size=float(np.median([r['size'] for r in R]))*cc/ce*0.97
    for r in R:
        w=text_width_mm(r['path'],r['size'],r['text'],r['cs'])*r['hs']; n=len(r['text'])-1
        w0=text_width_mm(np_,size,r['text'],0)
        cs=min(0.5,max(-0.45,(w-w0)/n*PT))
        hs=w/text_width_mm(np_,size,r['text'],cs)
        r.update(path=np_,size=size,cs=cs,hs=hs,sw=0)
GROUP_SET={'flyer_back':[(['flyer_back:78','flyer_back:79','flyer_back:80'],D+'Cinzel-w600.ttf')]}
def group_set(L,ids,np_):
    # one size + weight for a row of matching labels; ink width preserved via tracking
    R=[r for r in L if r['id'] in ids]; size=float(np.median([r['size'] for r in R]))
    for r in R:
        w=text_width_mm(r['path'],r['size'],r['text'],r['cs'])*r['hs']; n=len(r['text'])-1
        cs=(w-text_width_mm(np_,size,r['text'],0))/n*PT
        r.update(path=np_,size=size,cs=cs,hs=1.0,sw=0)
BODY_THICKEN={'flyer_front':0.013,'flyer_back':0.005}
BODY_THICKEN_ID={f'flyer_front:{i}':0.022 for i in range(76,91)}
SCRIPT_THICKEN={'flyer_front':0.012}
ITALIC_PREFIX={'flyer_front:73':'Invest'}
FB_FIX=[(823,143,851,181)]
FF_DOTS=[(410.5,546.75,5.6,(205,152,70)),(563.9,546.8,5.6,(205,152,70))]
FF_CLONE=[(333,591,343,600,14),(627,591,637,600,-14)]
FF_HEART_FIX=[(402,538,419,556),(556,538,572,556),(630,1502,660,1526),(216,1141,240,1161),(343,418,367,442)]
FF_HEARTS=[(644.5,1513.8,17.0,(250,250,246),0.55),(228.5,1151.0,15.0,(25,24,25),0.55),(354.7,429.8,17.2,(25,24,25),0.55)]
def fitted_side(c,side):
    S=SIDE[side]; pw,ph=S['page']; s=S['s']; off=S['off']
    L=json.load(open(f'{W}/fit_{side}.json'))
    from harmonize import harmonize
    for rid,h in harmonize(L).items():   # one body size per paragraph block, widths preserved
        r=next(x for x in L if x['id']==rid); r.update(h)
    if side in HEAD_SWAP:
        head_swap(L,*HEAD_SWAP[side])
    for (ids,np_) in GROUP_SET.get(side,[]):
        group_set(L,ids,np_)
    for r in L:
        if (side,r['cclass']) in COLOR_OVERRIDE: r['color']=COLOR_OVERRIDE[(side,r['cclass'])]
        if r['id'] in WEIGHT_OVERRIDE:   # lighter file, same set width
            np_=WEIGHT_OVERRIDE[r['id']]; kk=s*PT
            w0=text_width_mm(r['path'],r['size']*kk,r['text'],r['cs']*kk); w1=text_width_mm(np_,r['size']*kk,r['text'],r['cs']*kk)
            r['hs']*=w0/w1; r['path']=np_
        ox,oy=r['origin']
        x=off[0]+ox*s; y=off[1]+oy*s
        k=s*PT
        text=r['text'].replace("'","\u2019")
        if r['id'] in ITALIC_PREFIX:
            pre=ITALIC_PREFIX[r['id']]; rest=text[len(pre):]
            ip=D+'CrimsonText-Italic.ttf'
            w_reg=text_width_mm(r['path'],r['size']*k,pre,r['cs']*k)*r['hs']
            w_it=text_width_mm(ip,r['size']*k,pre,r['cs']*k)*r['hs']
            x2=x+(w_reg-w_it)/2
            draw_text(c,ph,x2,y,pre,ip,r['size']*k,r['cs']*k,r['hs'],r['color'],r['cclass'])
            draw_text(c,ph,x2+w_it+r['cs']*k/PT*r['hs'],y,rest,r['path'],r['size']*k,r['cs']*k,r['hs'],r['color'],r['cclass'])
            continue
        swpt=2*r['sw']*k
        if SCRIPT_THICKEN.get(side) and 'Parisienne' in r['path'] and r['sw']==0 and r['size']*k<12 and not r['cclass'].startswith('light'):
            swpt=SCRIPT_THICKEN[side]*r['size']*k   # small dark script: match heavier original stroke
        if side in BODY_THICKEN and os.path.basename(r['path'])=='CrimsonText-Regular.ttf' and not r['cclass'].startswith('light'):
            swpt=BODY_THICKEN_ID.get(r['id'],BODY_THICKEN[side])*r['size']*k     # original body copy is set heavier than Crimson Regular
        draw_text(c,ph,x,y,text,r['path'],r['size']*k,r['cs']*k,r['hs'],r['color'],r['cclass'],swpt,r['angle'])
    if side=='flyer_front':
        for (hx,hy,hw,col,lw) in FF_HEARTS:
            heart(c,ph,off[0]+hx*s,off[1]+hy*s,hw*s,rgb=col,lw=lw)
        for (dx,dy,dd,col) in FF_DOTS:
            c.saveState(); c.setFillColorCMYK(*cmyk(col)); c.circle((off[0]+dx*s)*mm,(ph-(off[1]+dy*s))*mm,dd*s/2*mm,stroke=0,fill=1); c.restoreState()
    return L
def heart(c,ph,cx,cy,w,rgb=GOLD,lw=0.35,fill=False):
    # heart centred at (cx,cy) mm, width w mm
    col=cmyk(rgb); c.saveState(); c.setStrokeColorCMYK(*col); c.setFillColorCMYK(*col); c.setLineWidth(lw); c.setLineJoin(1)
    X=lambda u:(cx+u*w/2)*mm; Y=lambda v:(ph-(cy+v*w/2))*mm
    p=c.beginPath()
    p.moveTo(X(0),Y(0.95))
    p.curveTo(X(-0.35),Y(0.6),X(-1.0),Y(0.2),X(-1.0),Y(-0.3))
    p.curveTo(X(-1.0),Y(-0.85),X(-0.3),Y(-1.0),X(0),Y(-0.45))
    p.curveTo(X(0.3),Y(-1.0),X(1.0),Y(-0.85),X(1.0),Y(-0.3))
    p.curveTo(X(1.0),Y(0.2),X(0.35),Y(0.6),X(0),Y(0.95))
    p.close()
    c.drawPath(p,stroke=1,fill=1 if fill else 0); c.restoreState()
def hline(c,ph,x0,x1,y,rgb=GOLD,lw=0.4):
    col=cmyk(rgb); c.saveState(); c.setStrokeColorCMYK(*col); c.setLineWidth(lw); c.line(x0*mm,(ph-y)*mm,x1*mm,(ph-y)*mm); c.restoreState()

# ---------------- card back ----------------
CB=dict(green=None,navy=None)
def cb_colors():
    fb=json.load(open(f'{W}/fit_flyer_back.json'))
    g=next(r['color'] for r in fb if r['cclass']=='green'); n=next(r['color'] for r in fb if r['cclass']=='navy_dark')
    return g,n
def text_width_mm(fpath,size_pt,text,cs_pt=0):
    from PIL import ImageFont
    f=ImageFont.truetype(fpath,200)
    wpt=sum(f.getlength(ch) for ch in text)/200*size_pt+cs_pt*(len(text)-1)
    return wpt/PT
def composite(bgimg,patch,alpha,x_mm,y_mm,w_mm,dpi):
    ppm=dpi/25.4
    wpx=int(round(w_mm*ppm)); hpx=int(round(patch.shape[0]*wpx/patch.shape[1]))
    P=cv2.resize(patch,(wpx,hpx),interpolation=cv2.INTER_AREA if wpx<patch.shape[1] else cv2.INTER_CUBIC).astype(np.float32)
    A=cv2.resize(alpha,(wpx,hpx),interpolation=cv2.INTER_LINEAR)[...,None]
    x0=int(round(x_mm*ppm-wpx/2)); y0=int(round(y_mm*ppm-hpx/2))
    reg=bgimg[y0:y0+hpx,x0:x0+wpx].astype(np.float32)
    bgimg[y0:y0+hpx,x0:x0+wpx]=np.clip(P*A+reg*(1-A),0,255).astype(np.uint8)
ICONS=[('IMMUNE','SUPPORT',(76,406)),('ENERGY &','FATIGUE',(76,519)),('SKIN','CONDITIONS',(75,877)),('DIGESTIVE','HEALTH',(76,643)),('HORMONE','BALANCE',(75,759))]
def card_back_bg(dpi=600,layout=None):
    im,_=bg.card_back_clean(dpi)
    # remove the flyer's gold frame line that the leafy-corner crops carry into the outer bleed
    band=int(round(1.8*dpi/25.4)); m=np.zeros(im.shape[:2],np.uint8); d=im.astype(int)
    gold=((d[...,2]-d[...,0])>45).astype(np.uint8)*255
    m[:,:band]=gold[:,:band]; m[:,-band:]=gold[:,-band:]
    m=cv2.dilate(m,np.ones((5,5),np.uint8)); im=cv2.inpaint(im,m,7,cv2.INPAINT_TELEA)
    target=np.median(im[600:800,800:1500].reshape(-1,3),0).astype(np.float32)
    up=cv2.imread(f'{W}/up_flyer_back.png')
    for (_,_,(x,y)),cx in zip(ICONS,layout['icon_x']):
        R=48*4; crop=up[y*4-R:y*4+R,x*4-R:x*4+R].astype(np.float32)
        yy,xx=np.mgrid[-R:R,-R:R]; rr=np.hypot(xx,yy)/4
        # tint cream of crop to card cream (weighted by lightness)
        ring=(rr>44.5)&(rr<47.5); src=np.median(crop[ring],0)
        L=crop.mean(2,keepdims=True)/255; wgt=np.clip((L-0.6)/0.3,0,1)
        crop=crop+(target-src)*wgt
        a=np.clip((46.5-rr)/2.5,0,1).astype(np.float32)
        composite(im,crop,a,cx+5,layout['icon_y']+5,layout['icon_d']*(2*48)/(2*43.5),dpi)
    # divider ornament from original card back
    src=cv2.imread('/workspace/medella_out/src_card_back.png').astype(np.float32)
    x0,x1,y0,y1=706,888,80,124
    crop=src[y0:y1,x0:x1]
    creamc=np.median(src[60:75,700:890].reshape(-1,3),0)
    lab=lambda a: cv2.cvtColor(np.clip(a,0,255).astype(np.uint8)[None] if a.ndim==1 else np.clip(a,0,255).astype(np.uint8),cv2.COLOR_BGR2LAB).astype(np.float32)
    d=np.linalg.norm(lab(crop)-lab(np.tile(creamc,(crop.shape[0],crop.shape[1],1))),axis=2)
    a=np.clip((d-6)/22,0,1)
    # suppress the thin gold rules in the crop outside the ornament core (vector lines drawn instead)
    core=np.zeros_like(a); core[:, 0:182]=1
    a=cv2.GaussianBlur(a,(0,0),0.6)
    crop=crop+(target-creamc)*np.clip((crop.mean(2,keepdims=True)/255-0.6)/0.3,0,1)
    composite(im,crop,a.astype(np.float32),45+5,layout['div_y']+5,layout['div_w'],dpi)
    return im
def card_back_page(c,dpi=600):
    pw,ph=100,60; g,n=cb_colors()
    lay=dict(icon_y=27.0,icon_d=8.6,div_y=12.2,div_w=11.5)
    cinzel7=D+'Cinzel-w700.ttf'; cinzel6=D+'Cinzel-w600.ttf'; par=D+'Parisienne-Regular.ttf'
    lbl_pt=7.0
    # icon x positions: spread so labels keep >=1.8mm gaps and >=3.5mm from trim
    LCS=0.05
    wids=[max(text_width_mm(cinzel7,lbl_pt,a,LCS),text_width_mm(cinzel7,lbl_pt,b,LCS)) for a,b,_ in ICONS]
    lo,hi=4.2,85.8; g_=(hi-lo-sum(wids))/4; xs=[]; x_=lo
    for wd in wids: xs.append(x_+wd/2); x_+=wd+g_
    lay['label_gap']=g_
    lay['icon_x']=xs
    im=card_back_bg(dpi,lay)
    p=f'{W}/bg_card_back_final.png'; cv2.imwrite(p,im)
    set_boxes(c,pw,ph); bg_image(c,p,pw,ph)
    T=lambda x,y,*a,**k: draw_text(c,ph,x+5,y+5,*a,**k)
    # title
    title='LIVE BLOOD ANALYSIS'; tsz=9.6; tcs=1.1
    tw=text_width_mm(cinzel7,tsz,title,tcs); T(45-tw/2,8.9,title,cinzel7,tsz,tcs,1.0,g,'green')
    # divider gold rules
    hline(c,ph,5+45-22,5+45-lay['div_w']/2-0.3,5+lay['div_y'],lw=0.45); hline(c,ph,5+45+lay['div_w']/2+0.3,5+45+22,5+lay['div_y'],lw=0.45)
    # script
    sc='A wide range of conditions'; ssz=17.5
    sw=text_width_mm(par,ssz,sc); T(45-sw/2,19.9,sc,par,ssz,0,1.0,g,'green',sw_pt=0.25)
    # labels
    info=[]
    for (a,b,_),x,wd in zip(ICONS,xs,wids):
        for j,tx in enumerate((a,b)):
            w_=text_width_mm(cinzel7,lbl_pt,tx,LCS); T(x-w_/2,35.0+j*2.95,tx,cinzel7,lbl_pt,LCS,1.0,n,'navy_dark')
            info.append((tx,x-w_/2,x+w_/2))
    # bottom line
    words=['DETECT','BALANCE','HEAL','LIVE BETTER']; bsz=7.0; bcs=0.9; gap=4.2
    ws=[text_width_mm(cinzel6,bsz,w_,bcs) for w_ in words]
    total=sum(ws)+gap*3; x=45-total/2; yb=44.6
    for i,(w_,wd) in enumerate(zip(words,ws)):
        T(x,yb,w_,cinzel6,bsz,bcs,1.0,n,'navy_dark'); x+=wd
        if i<3: heart(c,ph,5+x+gap/2,5+yb-0.95,1.25,lw=0.3,fill=True); x+=gap
    return dict(label_gap=lay['label_gap'],title_w=tw,script_w=sw,labels=info,bottom=(45-total/2,45+total/2),icon_x=xs)

def build(dpi_fl=400,dpi_card=600):
    os.makedirs(OUT,exist_ok=True)
    # background PNGs
    ff,_=bg.flyer_front(dpi_fl)
    S=SIDE['flyer_front']; k=dpi_fl/25.4
    msk=np.zeros(ff.shape[:2],np.uint8)
    for (x0,y0,x1,y1) in FF_HEART_FIX:
        X0,Y0,X1,Y1=[int(round((S['off'][i%2]+v*S['s'])*k)) for i,v in enumerate((x0,y0,x1,y1))]
        msk[Y0:Y1,X0:X1]=255
    ff=cv2.inpaint(ff,msk,9,cv2.INPAINT_TELEA)
    for (x0,y0,x1,y1,dx) in FF_CLONE:   # copy neighbouring rule segment over erase specks
        X0,Y0,X1,Y1=[int(round((S['off'][i%2]+v*S['s'])*k)) for i,v in enumerate((x0,y0,x1,y1))]
        D_=int(round(dx*S['s']*k)); ff[Y0:Y1,X0:X1]=ff[Y0:Y1,X0+D_:X1+D_]
    cv2.imwrite(f'{W}/bgF_flyer_front.png',ff)
    fb,_,_,_=bg.flyer_back(dpi_fl)
    S=SIDE['flyer_back']; msk=np.zeros(fb.shape[:2],np.uint8)
    for (x0,y0,x1,y1) in FB_FIX:
        X0,Y0,X1,Y1=[int(round((S['off'][i%2]+v*S['s'])*k)) for i,v in enumerate((x0,y0,x1,y1))]
        msk[Y0:Y1,X0:X1]=255
    fb=cv2.inpaint(fb,msk,9,cv2.INPAINT_TELEA)
    cv2.imwrite(f'{W}/bgF_flyer_back.png',fb)
    cf,_,_,_=bg.card_front(dpi_card); cv2.imwrite(f'{W}/bgF_card_front.png',cf)
    rep={}
    for name,pages in [('flyer_A5_front_back',['flyer_front','flyer_back']),('business_card_front_back',['card_front','card_back'])]:
        c=canvas.Canvas(f'{W}/rgb_{name}.pdf')
        c.setTitle(f'Medella {name.replace("_"," ")} press'); c.setAuthor('Flyerz'); 
        for sd in pages:
            if sd=='card_back':
                rep['card_back']=card_back_page(c)
            else:
                pw,ph=SIDE[sd]['page']; set_boxes(c,pw,ph); bg_image(c,f'{W}/bgF_{sd}.png',pw,ph); fitted_side(c,sd)
            c.showPage()
        c.save()
    json.dump(rep,open(f'{W}/cardback_layout.json','w'),indent=1)
    return rep
if __name__=='__main__':
    print(build())
