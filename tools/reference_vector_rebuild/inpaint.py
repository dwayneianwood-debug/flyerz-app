import cv2, numpy as np, sys
def run(s):
    src=cv2.imread(f'/workspace/medella_out/src_{s}.png'); m=(cv2.imread(f'mask_{s}.png',0)>0).astype(np.uint8)
    m=cv2.dilate(m,np.ones((3,3),np.uint8))*255
    t=cv2.inpaint(src,m,5,cv2.INPAINT_TELEA).astype(np.float32)
    # local grain: high-pass std of the original background around the mask
    hp=src.astype(np.float32)-cv2.GaussianBlur(src.astype(np.float32),(0,0),2)
    ring=cv2.dilate(m,np.ones((15,15),np.uint8))-m
    sd=float(np.std(hp[ring>0])) if (ring>0).any() else 2.0
    rng=np.random.default_rng(3)
    noise=cv2.GaussianBlur(rng.normal(0,1,src.shape[:2]).astype(np.float32),(0,0),0.8)
    noise=noise/noise.std()*1.2
    mm=(cv2.GaussianBlur(m.astype(np.float32)/255,(5,5),0))[...,None]
    t=t+noise[...,None]*mm
    cv2.imwrite(f'clean_{s}.png',np.clip(t,0,255).astype(np.uint8)); print(s,'grain sd',round(sd,2))

def residual(s):
    import pickle
    c=cv2.imread(f'clean_{s}.png'); lab=cv2.cvtColor(c,cv2.COLOR_BGR2LAB).astype(np.float32)
    med=cv2.cvtColor(cv2.medianBlur(c,9),cv2.COLOR_BGR2LAB).astype(np.float32)
    d=np.linalg.norm(lab-med,axis=2)>28
    zone=np.zeros(d.shape,np.uint8)
    for L in pickle.load(open(f'meta_{s}.pkl','rb')):
        x0,y0,x1,y1=[int(v) for v in L['box']]
        if L['text'].strip().isdigit(): continue
        zone[max(0,y0-4):y1+4,max(0,x0-2):x1+2]=1
    d&=zone>0
    n,cc,st,_=cv2.connectedComponentsWithStats(d.astype(np.uint8),8)
    m=np.zeros(d.shape,np.uint8)
    for k in range(1,n):
        if st[k,4]<=12: m[cc==k]=255
    m=cv2.dilate(m,np.ones((5,5),np.uint8))
    out=cv2.inpaint(c,m,4,cv2.INPAINT_TELEA)
    cv2.imwrite(f'clean_{s}.png',out); print(s,'residual specks',int((m>0).sum()))
for s in sys.argv[1:]: run(s); residual(s)
