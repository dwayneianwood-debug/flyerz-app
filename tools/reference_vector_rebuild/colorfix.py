import cv2,numpy as np,shutil,os
def fix(up,ref,f,ox,oy,sigL=1.0):
    """up px = (ox + f*u, oy + f*v) for ref px (u,v). Returns up with ref's colour restored (Lab back-projection)."""
    H,W=ref.shape[:2]
    small=cv2.resize(up,None,fx=1/f,fy=1/f,interpolation=cv2.INTER_AREA)
    M=np.float32([[1,0,ox/f],[0,1,oy/f]])  # ref (u,v) -> small (u+ox/f, v+oy/f)
    d=cv2.warpAffine(small,M,(W,H),flags=cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP,borderMode=cv2.BORDER_REPLICATE)
    Lr=cv2.cvtColor(ref,cv2.COLOR_BGR2LAB).astype(np.float32); Ld=cv2.cvtColor(d,cv2.COLOR_BGR2LAB).astype(np.float32)
    res=Lr-Ld
    res[...,0]=cv2.GaussianBlur(res[...,0],(0,0),sigL)
    # residual back to up grid
    Mu=np.float32([[f,0,ox],[0,f,oy]])
    R=cv2.warpAffine(res,Mu,(up.shape[1],up.shape[0]),flags=cv2.INTER_CUBIC,borderMode=cv2.BORDER_CONSTANT,borderValue=0)
    # only inside ref footprint; feather edges
    m=np.zeros(up.shape[:2],np.float32)
    x0,y0=int(ox),int(oy); x1,y1=int(ox+f*W),int(oy+f*H)
    m[max(y0,0):y1,max(x0,0):x1]=1; m=cv2.GaussianBlur(m,(0,0),4)
    Lu=cv2.cvtColor(up,cv2.COLOR_BGR2LAB).astype(np.float32)+R*m[...,None]
    return cv2.cvtColor(np.clip(Lu,0,255).astype(np.uint8),cv2.COLOR_LAB2BGR)
jobs=[('up_flyer_front.png','clean_flyer_front.png',4,0,0),
      ('up_flyer_back.png','clean_flyer_back.png',4,0,0),
      ('up_card_back.png','clean_card_back.png',4,0,0),
      ('up_exp_card_pad.png','clean_card_front.png',0.645*4,199*4,0.645*4*124)]
for u,r,f,ox,oy in jobs:
    o=u.replace('.png','.orig.png')
    if not os.path.exists(o): shutil.copy(u,o)
    up=cv2.imread(o); ref=cv2.imread(r)
    out=fix(up,ref,f,ox,oy); cv2.imwrite(u,out)
    # verify
    small=cv2.resize(out,None,fx=1/f,fy=1/f,interpolation=cv2.INTER_AREA)
    d=cv2.warpAffine(small,np.float32([[1,0,ox/f],[0,1,oy/f]]),(ref.shape[1],ref.shape[0]),flags=cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP)
    s0=cv2.resize(up,None,fx=1/f,fy=1/f,interpolation=cv2.INTER_AREA); d0=cv2.warpAffine(s0,np.float32([[1,0,ox/f],[0,1,oy/f]]),(ref.shape[1],ref.shape[0]),flags=cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP)
    lab=lambda x:cv2.cvtColor(x,cv2.COLOR_BGR2LAB).astype(float)
    print(u,'mean dE before',np.sqrt(((lab(ref)-lab(d0))**2).sum(-1)).mean().round(2),'after',np.sqrt(((lab(ref)-lab(d))**2).sum(-1)).mean().round(2))
