import sys,json,pickle,numpy as np,cv2
sys.path.insert(0,'/workspace/medwork')
MM=25.4
SIDE={'flyer_front':(205/1536,((158-1024*205/1536)/2,7.5),'bgF_flyer_front.png',400),'flyer_back':(148/1054,(5,(220-1492*148/1054)/2),'bgF_flyer_back.png',400),'card_front':(50/992,((100-1586*50/992)/2,5),'bgF_card_front.png',600)}
out={}
for side,(s,off,png,dpi) in SIDE.items():
    B=cv2.imread(png); k=dpi/MM
    lab=cv2.cvtColor(B,cv2.COLOR_BGR2LAB).astype(np.float32)
    meta=pickle.load(open(f'meta_{side}.pkl','rb'))
    allm=np.zeros(B.shape[:2],np.uint8)
    # full text mask at page res
    for e in meta:
        x0,y0,x1,y1=e['ink']; m=e['mask'].astype(np.uint8)*255
        X0,Y0=int(round((off[0]+x0*s)*k)),int(round((off[1]+y0*s)*k)); X1,Y1=int(round((off[0]+x1*s)*k)),int(round((off[1]+y1*s)*k))
        mm=cv2.resize(m,(X1-X0,Y1-Y0),interpolation=cv2.INTER_NEAREST)
        allm[Y0:Y1,X0:X1]|=mm
    res=[]
    for e in meta:
        x0,y0,x1,y1=e['ink']; h=y1-y0
        X0,Y0=int(round((off[0]+(x0-3)*s)*k)),int(round((off[1]+(y0-2)*s)*k)); X1,Y1=int(round((off[0]+(x1+3)*s)*k)),int(round((off[1]+(y1+2)*s)*k))
        P=int(round(h*0.8*s*k))
        inner=np.zeros(B.shape[:2],bool); inner[Y0:Y1,X0:X1]=True
        ring=np.zeros_like(inner); ring[max(0,Y0-P):Y1+P,max(0,X0-P):X1+P]=True; ring&=~inner; ring&=(allm==0)
        m_in=inner&(allm>0)
        box_in=inner.copy()
        if m_in.sum()<10 or ring.sum()<50: continue
        Lin=lab[m_in]; Lr=lab[ring]
        dE=float(np.linalg.norm(np.median(Lin,0)-np.median(Lr,0))*100/255)  # approx dE (L scaled)
        # residual ghost: pixels in erased area deviating strongly from ring median
        med=np.median(Lr,0); dev=np.linalg.norm(Lin-med,axis=1)*100/255
        ring_dev=np.linalg.norm(Lr-med,axis=1)*100/255
        thr=max(12,np.percentile(ring_dev,99))
        ghost=float((dev>thr).mean())
        devb=np.linalg.norm(lab[box_in]-med,axis=1)*100/255
        # only count pixels deviating toward the text colour (darker for dark text, lighter for light text)
        tc=np.array(e['color'],np.float32); bgc=np.array(e['bg'],np.float32)
        Lb=lab[box_in][:,0]; dark_text=tc.mean()<bgc.mean()
        toward=(Lb<med[0]-thr*2.55) if dark_text else (Lb>med[0]+thr*2.55)
        ghost_box=float(((devb>thr)&toward).sum())
        res.append(dict(id=e['id'],text=e['text'],dE_patch=round(dE,2),ghost_frac=round(ghost,4),ghost_box_px=int(ghost_box),box=[X0,Y0,X1,Y1]))
    out[side]=res
    bad=[r for r in res if r['dE_patch']>2.5 or r['ghost_frac']>0.01 or r['ghost_box_px']>30]
    print(f'== {side}: {len(res)} erased areas checked; flagged {len(bad)}')
    for r in sorted(bad,key=lambda r:-r['ghost_frac']): print('   ',r['id'],r['text'][:30],'dE',r['dE_patch'],'ghost',r['ghost_frac'],'boxpx',r['ghost_box_px'])
json.dump(out,open('qa_bg.json','w'),indent=1)
