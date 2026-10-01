import cv2,numpy as np
exec(open('/workspace/medwork/colorfix2.py').read().split('jobs=')[0])
def warm_mask(L):
    a=L[...,1]-128; b=L[...,2]-128
    m=np.clip((b-12)/12,0,1)*np.clip((a+4)/6,0,1)*np.clip((40-a)/8,0,1)
    return m
jobs=[('up_flyer_front.png','clean_flyer_front.png',4,0,0),('up_flyer_back.png','clean_flyer_back.png',4,0,0)]
for u,r,f,ox,oy in jobs:
    up=cv2.imread(u.replace('.png','.orig.png')); ref=cv2.imread(r)
    # fit only on warm pixels
    H,W=ref.shape[:2]
    small=cv2.resize(up,None,fx=1/f,fy=1/f,interpolation=cv2.INTER_AREA)
    Lr=cv2.cvtColor(ref,cv2.COLOR_BGR2LAB).astype(np.float32); Ld=cv2.cvtColor(small,cv2.COLOR_BGR2LAB).astype(np.float32)
    sel=(warm_mask(Lr)>0.5).reshape(-1)|(warm_mask(Ld)>0.5).reshape(-1)
    X=feats(Ld)[sel]; Y=Lr.reshape(-1,3)[sel]
    B=np.linalg.lstsq(X,Y,rcond=None)[0]
    Lu=cv2.cvtColor(up,cv2.COLOR_BGR2LAB).astype(np.float32); out=np.empty_like(Lu)
    for y in range(0,up.shape[0],512):
        blk=Lu[y:y+512]; mp=(feats(blk)@B).reshape(blk.shape); m=warm_mask(blk)[...,None]
        out[y:y+512]=blk+m*(mp-blk)
    res=cv2.cvtColor(np.clip(out,0,255).astype(np.uint8),cv2.COLOR_LAB2BGR); cv2.imwrite(u,res)
    d=cv2.resize(res,(W,H),interpolation=cv2.INTER_AREA); Ld2=cv2.cvtColor(d,cv2.COLOR_BGR2LAB).astype(float)
    gold=(Lr[...,2]-128>25)&(Lr[...,1]-128>0)&(Lr[...,1]-128<30); cream=Lr[...,0]>225; dark=Lr[...,0]<60; green=Lr[...,1]-128<-8
    for n,mm in (('gold',gold),('cream',cream),('dark',dark),('green',green)):
        print(u,n,'src',Lr[mm].mean(0).round(1),'orig',Ld[mm].mean(0).round(1),'fix',Ld2[mm].mean(0).round(1))
