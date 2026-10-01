import cv2,numpy as np
def feats(L):
    L=L.reshape(-1,3)/255.0; l,a,b=L[:,0],L[:,1],L[:,2]
    return np.stack([np.ones_like(l),l,a,b,l*l,a*a,b*b,l*a,l*b,a*b,l*l*l,a*a*a,b*b*b],1)
def fit_apply(up,ref,f,ox,oy):
    H,W=ref.shape[:2]
    small=cv2.resize(up,None,fx=1/f,fy=1/f,interpolation=cv2.INTER_AREA)
    d=cv2.warpAffine(small,np.float32([[1,0,ox/f],[0,1,oy/f]]),(W,H),flags=cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP,borderMode=cv2.BORDER_REPLICATE)
    Lr=cv2.cvtColor(ref,cv2.COLOR_BGR2LAB).astype(np.float32); Ld=cv2.cvtColor(d,cv2.COLOR_BGR2LAB).astype(np.float32)
    # weight: emphasise chromatic pixels so gold/green get fitted well
    ch=np.hypot(Lr[...,1]-128,Lr[...,2]-128).reshape(-1)
    w=np.sqrt(1+ch/10)
    X=feats(Ld); Y=Lr.reshape(-1,3)
    Xw=X*w[:,None]; lam=1e-3*np.eye(X.shape[1])
    B=np.linalg.solve(Xw.T@Xw+lam*len(X),Xw.T@(Y*w[:,None]))
    pred=X@B; print('  fit dE before',np.sqrt(((Y-Ld.reshape(-1,3))**2).sum(1)).mean().round(2),'after',np.sqrt(((Y-pred)**2).sum(1)).mean().round(2))
    Lu=cv2.cvtColor(up,cv2.COLOR_BGR2LAB).astype(np.float32); out=np.empty_like(Lu)
    for y in range(0,up.shape[0],512):
        blk=Lu[y:y+512]; out[y:y+512]=(feats(blk)@B).reshape(blk.shape)
    return cv2.cvtColor(np.clip(out,0,255).astype(np.uint8),cv2.COLOR_LAB2BGR),B
jobs=[('up_flyer_front.png','clean_flyer_front.png',4,0,0),
      ('up_flyer_back.png','clean_flyer_back.png',4,0,0),
      ('up_exp_card_pad.png','clean_card_front.png',0.645*4,199*4,0.645*4*124)]
Bs={}
for u,r,f,ox,oy in jobs:
    print(u); up=cv2.imread(u.replace('.png','.orig.png')); ref=cv2.imread(r)
    out,B=fit_apply(up,ref,f,ox,oy); cv2.imwrite(u,out); Bs[u]=B
np.save('colorfix_B.npy',Bs,allow_pickle=True)
# flyer front base layer (outside art) uses same transform as art for consistency
up=cv2.imread('up_exp_flyer_front.orig.png') if __import__('os').path.exists('up_exp_flyer_front.orig.png') else None
