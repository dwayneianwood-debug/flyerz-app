import sys, json, pickle, numpy as np, cv2
sys.path.insert(0,'/workspace/medwork')
from spec import lines
def run(side):
    bgr=cv2.imread(f'/workspace/medella_out/src_{side}.png')
    lab=cv2.cvtColor(bgr,cv2.COLOR_BGR2LAB).astype(np.float32)
    H,W=bgr.shape[:2]
    erase=np.zeros((H,W),np.uint8); meta=[]
    hsv=cv2.cvtColor(bgr,cv2.COLOR_BGR2HSV)
    GOLD=(hsv[:,:,0]>=10)&(hsv[:,:,0]<=38)&(hsv[:,:,1]>60)&(hsv[:,:,2]>90)
    for L in lines(side):
        q=np.array(L['quad'],np.float32)
        h=max(np.linalg.norm(q[3]-q[0]),np.linalg.norm(q[2]-q[1]))
        pad=min(6,max(3,int(round(h*0.25))))
        digit=L['text'].strip().isdigit() and len(L['text'].strip())<=2
        poly=np.zeros((H,W),np.uint8); cv2.fillPoly(poly,[q.round().astype(np.int32)],1)
        if not digit: poly=cv2.dilate(poly,np.ones((2*pad+1,5),np.uint8))
        core0=np.zeros((H,W),np.uint8); cv2.fillPoly(core0,[q.round().astype(np.int32)],1)
        bg=np.median(lab[core0>0],axis=0)
        dist=np.linalg.norm(lab-bg,axis=2)
        txt0=(dist>26)&(poly>0)
        dd=dist[txt0]; thr=np.percentile(dd,60) if dd.size else 99
        tcol=np.median(lab[txt0&(dist>=thr)],axis=0) if dd.size else bg
        dt=np.linalg.norm(lab-tcol,axis=2)
        txt=txt0&(dt<dist*1.1)
        tc_bgr=cv2.cvtColor(np.uint8([[tcol]]),cv2.COLOR_LAB2BGR)[0,0]
        tc_h,tc_s,tc_v=cv2.cvtColor(np.uint8([[tc_bgr]]),cv2.COLOR_BGR2HSV)[0,0]
        if not (12<=tc_h<=38 and tc_s>60):
            txt&=~GOLD
        # remove tiny specks
        n,cc,st,_=cv2.connectedComponentsWithStats(txt.astype(np.uint8),8)
        keep=np.zeros_like(txt)
        for k in range(1,n):
            w_,h_=st[k,2],st[k,3]
            comp=(cc==k)
            inside=(core0[comp]>0).mean() if st[k,4] else 0
            cy_,cx_=int(st[k,1]+st[k,3]/2),int(st[k,0]+st[k,2]/2)
            small_ok=st[k,4]<=60 and core0[min(H-1,cy_),min(W-1,cx_)]>0
            if st[k,4]>=2 and not (w_>8*h_ and h_<0.25*h) and (inside>=0.3 or small_ok): keep|=comp
        core=(dist>45)&keep
        col=np.median(bgr[core if core.sum()>10 else keep][:, ::-1],axis=0) if keep.sum() else np.array([0,0,0])
        ys,xs=np.where(keep)
        if len(xs)==0: print('EMPTY',L['id']); continue
        e=cv2.dilate(keep.astype(np.uint8),np.ones((3,3) if digit else (5,5),np.uint8))&poly
        erase|=e
        x0,x1,y0,y1=xs.min(),xs.max()+1,ys.min(),ys.max()+1
        meta.append({**L,'ink':[int(x0),int(y0),int(x1),int(y1)],'color':[float(c) for c in col],
                     'bg':[float(v) for v in cv2.cvtColor(np.uint8([[bg]]),cv2.COLOR_LAB2RGB)[0,0]],
                     'mask':keep[y0:y1,x0:x1].copy(),'quad':q.tolist()})
    cv2.imwrite(f'mask_{side}.png',erase*255)
    ov=bgr.copy(); ov[erase>0]=(0,0,255)
    cv2.imwrite(f'maskov_{side}.png',ov)
    pickle.dump(meta,open(f'meta_{side}.pkl','wb'))
    print(side,len(meta),'mask px',int(erase.sum()))
for s in sys.argv[1:]: run(s)
