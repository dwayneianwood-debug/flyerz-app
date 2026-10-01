import cv2,numpy as np,sys
def fix(up,ref,sigL=0.8,Lgain=1.0):
    f=up.shape[1]/ref.shape[1]
    Lr=cv2.cvtColor(ref,cv2.COLOR_BGR2LAB).astype(np.float32)
    Lu=cv2.cvtColor(up,cv2.COLOR_BGR2LAB).astype(np.float32)
    big=cv2.resize(Lr,(up.shape[1],up.shape[0]),interpolation=cv2.INTER_CUBIC)
    ab=cv2.GaussianBlur(big[...,1:],(0,0),f*0.35)
    small=cv2.resize(up,(ref.shape[1],ref.shape[0]),interpolation=cv2.INTER_AREA)
    Ls=cv2.cvtColor(small,cv2.COLOR_BGR2LAB).astype(np.float32)[...,0]
    res=cv2.GaussianBlur(Lr[...,0]-Ls,(0,0),sigL)
    R=cv2.resize(res,(up.shape[1],up.shape[0]),interpolation=cv2.INTER_CUBIC)
    out=np.dstack([Lu[...,0]+Lgain*R,ab[...,0],ab[...,1]])
    return cv2.cvtColor(np.clip(out,0,255).astype(np.uint8),cv2.COLOR_LAB2BGR)
if __name__=='__main__':
    for u,r in [('up_flyer_front.png','clean_flyer_front.png'),('up_flyer_back.png','clean_flyer_back.png')]:
        up=cv2.imread(u.replace('.png','.orig.png')); ref=cv2.imread(r)
        cv2.imwrite(u,fix(up,ref)); print('wrote',u)

def fix_affine(up,ref,f,ox,oy,sigL=0.8):
    H,W=ref.shape[:2]; UH,UW=up.shape[:2]
    Lr=cv2.cvtColor(ref,cv2.COLOR_BGR2LAB).astype(np.float32); Lu=cv2.cvtColor(up,cv2.COLOR_BGR2LAB).astype(np.float32)
    Mu=np.float32([[f,0,ox],[0,f,oy]])  # ref->up
    big=cv2.warpAffine(Lr,Mu,(UW,UH),flags=cv2.INTER_CUBIC,borderMode=cv2.BORDER_REPLICATE)
    ab=cv2.GaussianBlur(big[...,1:],(0,0),f*0.35)
    small=cv2.resize(up,None,fx=1/f,fy=1/f,interpolation=cv2.INTER_AREA)
    d=cv2.warpAffine(small,np.float32([[1,0,ox/f],[0,1,oy/f]]),(W,H),flags=cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP,borderMode=cv2.BORDER_REPLICATE)
    res=cv2.GaussianBlur(Lr[...,0]-cv2.cvtColor(d,cv2.COLOR_BGR2LAB).astype(np.float32)[...,0],(0,0),sigL)
    R=cv2.warpAffine(res,Mu,(UW,UH),flags=cv2.INTER_CUBIC,borderMode=cv2.BORDER_REPLICATE)
    m=np.zeros((UH,UW),np.float32); x0,y0,x1,y1=int(ox),int(oy),int(ox+f*W),int(oy+f*H)
    m[max(y0,0)+6:y1-6,max(x0,0)+6:x1-6]=1; m=cv2.GaussianBlur(m,(0,0),3)[...,None]
    out=np.dstack([Lu[...,0]+R,ab[...,0],ab[...,1]])
    out=Lu*(1-m)+out*m
    return cv2.cvtColor(np.clip(out,0,255).astype(np.uint8),cv2.COLOR_LAB2BGR)
