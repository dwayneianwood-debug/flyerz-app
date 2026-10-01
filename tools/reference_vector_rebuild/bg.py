import cv2, numpy as np
from PIL import Image
Image.MAX_IMAGE_PIXELS=None
MM=25.4
def A(sx,tx,sy,ty):  # 2x3 affine
    return np.array([[sx,0,tx],[0,sy,ty]],np.float64)
def warp(img,M_page2img,W,H,border=cv2.BORDER_REPLICATE):
    return cv2.warpAffine(img,M_page2img,(W,H),flags=cv2.INTER_AREA|cv2.WARP_INVERSE_MAP if False else cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP,borderMode=border)
def prescale(img,f):
    # downscale large images with area filter first to avoid aliasing
    if f<0.9:
        return cv2.resize(img,None,fx=f,fy=f,interpolation=cv2.INTER_AREA),f
    return img,1.0
def place(img, img_px_per_src, src_off_in_img, s_mm, off_mm, W,H,dpi,border=cv2.BORDER_REPLICATE):
    """img coords = src_off + img_px_per_src*src; src = (page_mm-off)/s ; page_mm = page_px*MM/dpi"""
    ppm=dpi/MM
    target=img_px_per_src/(s_mm*ppm)  # img px per page px
    img2,f=prescale(img,1/target)
    k=img_px_per_src*f/(s_mm*ppm)
    ox=src_off_in_img[0]*f - img_px_per_src*f*off_mm[0]/s_mm
    oy=src_off_in_img[1]*f - img_px_per_src*f*off_mm[1]/s_mm
    M=A(k,ox,k,oy)
    return cv2.warpAffine(img2,M,(W,H),flags=cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP,borderMode=border)
def art_rect_px(src_wh,s_mm,off_mm,dpi):
    ppm=dpi/MM
    x0=off_mm[0]*ppm; y0=off_mm[1]*ppm
    return x0,y0,x0+src_wh[0]*s_mm*ppm,y0+src_wh[1]*s_mm*ppm
def soften_outside(img,rect,ksize):
    x0,y0,x1,y1=[int(round(v)) for v in rect]
    b=cv2.GaussianBlur(img,(0,0),ksize)
    m=np.ones(img.shape[:2],np.float32); m[max(y0,0):y1,max(x0,0):x1]=0
    m=cv2.GaussianBlur(m,(0,0),ksize/2)[...,None]
    return (img*(1-m)+b*m).astype(np.uint8)

def flyer_front(dpi=400):
    W,H=int(round(158/MM*dpi)),int(round(220/MM*dpi)); ppm=dpi/MM
    s=205/1536; off=((158-1024*205/1536)/2,7.5)
    E=cv2.imread('up_exp_flyer_front.png')
    base=place(E,0.666*4,(171*4,0),s,off,W,H,dpi,border=cv2.BORDER_REFLECT)
    art=cv2.imread('up_flyer_front.png')
    artp=place(art,4,(0,0),s,off,W,H,dpi,border=cv2.BORDER_REFLECT)
    x0,y0,x1,y1=art_rect_px((1024,1536),s,off,dpi)
    # vertical outside rows: replicate from art, then soften
    fe=1.5*ppm
    xs=np.arange(W,dtype=np.float32)
    ax=np.clip(np.minimum(xs-x0,x1-xs)/fe,0,1)
    alpha=np.repeat(ax[None,:],H,0)[...,None]
    out=(artp*alpha+base*(1-alpha)).astype(np.uint8)
    # extend above/below art from edge band (no mirrored content, no streaks)
    out=extend_rows(out,y0,y1,ppm)
    # art-corner specks at seam: inpaint small discs at the four art corners
    m=np.zeros((H,W),np.uint8)
    for cx in (x0,x1):
        for cy in (y0,y1):
            cv2.ellipse(m,(int(round(cx)),int(round(cy))),(10,22),0,0,360,255,-1)
    out=cv2.inpaint(out,m,6,cv2.INPAINT_TELEA)
    return out,(W,H)
def extend_rows(img,y0,y1,ppm):
    H,W=img.shape[:2]; out=img.astype(np.float32)
    iy0=int(np.ceil(y0)); iy1=int(np.floor(y1))
    band=int(round(0.6*ppm)); rng=np.random.default_rng(7)
    for top in (True,False):
        if top:
            e=out[iy0+1:iy0+1+band].mean(0); rows=range(iy0,-1,-1); ref=out[iy0+1:iy0+1+int(3*ppm)]
        else:
            e=out[iy1-1-band:iy1-1].mean(0); rows=range(iy1,H); ref=out[iy1-1-int(3*ppm):iy1-1]
        # grain level from reference band high-pass
        hp=ref-cv2.GaussianBlur(ref,(0,0),2); gs=float(np.std(hp))
        for i,y in enumerate(rows):
            d=i/ppm  # mm from art edge
            sig=max(0.6*ppm,(0.6+0.5*d)*ppm)
            row=cv2.GaussianBlur(e[None,:,:],(0,0),sigmaX=sig,sigmaY=0.01)[0]
            row=row+rng.normal(0,gs,row.shape).astype(np.float32)
            a=min(1.0,(i+1)/(0.5*ppm))
            out[y]=out[y]*(1-a)+row*a
    # light blur of the whole outside zone to merge grain rows
    b=cv2.GaussianBlur(out,(0,0),0.6)
    m=np.zeros((H,1,1),np.float32); m[:iy0]=1; m[iy1:]=1
    out=out*(1-m)+b*m
    return np.clip(out,0,255).astype(np.uint8)
def soften_rows(img,y0,y1,ppm):
    H=img.shape[0]
    b=cv2.GaussianBlur(img,(0,0),0.8*ppm)
    ys=np.arange(H,dtype=np.float32)
    d=np.maximum(y0-ys,ys-y1)  # >0 outside
    m=np.clip(d/(0.8*ppm),0,1)[:,None,None]
    return (img*(1-m)+b*m).astype(np.uint8)

def flyer_back(dpi=400):
    W,H=int(round(158/MM*dpi)),int(round(220/MM*dpi)); ppm=dpi/MM
    s=148/1054; off=(5,(220-1492*s)/2)
    art=cv2.imread('up_flyer_back.png')
    out=place(art,4,(0,0),s,off,W,H,dpi)
    r=art_rect_px((1054,1492),s,off,dpi)
    out=soften_outside(out,r,1.0*ppm)
    return out,(W,H),s,off

def card_front(dpi=600):
    W,H=int(round(100/MM*dpi)),int(round(60/MM*dpi)); ppm=dpi/MM
    s=50/992; off=((100-1586*s)/2,5)   # art height == trim height (50 mm); nothing cut
    E=cv2.imread('up_exp_card_pad.png')
    # cardpad src = card src + (0,124); in exp: 199 + 0.645*u , 0 + 0.645*(v+124)
    out=place(E,0.645*4,(199*4,0.645*4*124),s,off,W,H,dpi)
    y0=int(round(off[1]*ppm)); y1=int(round((off[1]+992*s)*ppm))
    # bleed rows (outside trim only): mirror of art rows, lightly softened
    out[:y0]=out[y0:2*y0][::-1]; n=H-y1; out[y1:]=out[y1-n:y1][::-1]
    out=soften_rows(out,y0,y1,ppm)
    return out,(W,H),s,off

def card_back(dpi=600):
    W,H=int(round(100/MM*dpi)),int(round(60/MM*dpi)); ppm=dpi/MM
    E=cv2.imread('up_card_back.png')  # 4096x2556
    s=100/E.shape[1]; off=(0,(60-E.shape[0]*s)/2)
    out=place(E,1,(0,0),s,off,W,H,dpi)
    return out,(W,H)

if __name__=='__main__':
    import time
    t=time.time()
    ff,_=flyer_front(); cv2.imwrite('bg_flyer_front.png',ff)
    fb,_,s,off=flyer_back(); cv2.imwrite('bg_flyer_back.png',fb); print('fb off',off)
    cf,_,s,off=card_front(); cv2.imwrite('bg_card_front.png',cf); print('cf off',off)
    cb,_=card_back(); cv2.imwrite('bg_card_back.png',cb)
    print(time.time()-t)

def card_back_clean(dpi=600):
    out,(W,H)=card_back(dpi)
    f=W/709.0  # preview coords were at 709 px width
    keep=np.zeros((H,W),np.float32)
    for (x0,y0,x1,y1) in [(0,0,140,112),(572,0,709,104),(0,318,195,425),(600,312,709,425)]:
        keep[int(y0*f):int(y1*f),int(x0*f):int(x1*f)]=1
    # grow keep to include actual leaf pixels near those zones (dark/saturated vs cream)
    lab=cv2.cvtColor(out,cv2.COLOR_BGR2LAB).astype(np.float32)
    keep=cv2.GaussianBlur(keep,(0,0),6*f)
    keep=np.clip(keep*1.6,0,1)
    # smooth cream: masked normalised blur excluding keep zones
    wgt=(1-keep)
    sig=40*f/3
    num=cv2.GaussianBlur(out.astype(np.float32)*wgt[...,None],(0,0),sig)
    den=cv2.GaussianBlur(wgt,(0,0),sig)[...,None]+1e-4
    smooth=num/den
    rng=np.random.default_rng(7)
    n=rng.normal(0,1.6,(H,W)).astype(np.float32); n=cv2.GaussianBlur(n,(0,0),0.9)*1.8
    smooth=smooth+n[...,None]
    res=out*keep[...,None]+smooth*(1-keep[...,None])
    return np.clip(res,0,255).astype(np.uint8),(W,H)
