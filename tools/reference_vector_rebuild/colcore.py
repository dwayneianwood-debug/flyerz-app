import json,sys,numpy as np,cv2
sys.path.insert(0,'.')
from spec import lines
pre=sys.argv[1] if len(sys.argv)>1 else 'g'
SIDE={'flyer_front':(205/1536,((158-1024*205/1536)/2,7.5),f'{pre}_fly-1.png'),'flyer_back':(148/1054,(5,(220-1492*148/1054)/2),f'{pre}_fly-2.png'),'card_front':(50/992,((100-1586*50/992)/2,5),f'{pre}_card-1.png')}
k=300/25.4
for side,(s,off,png) in SIDE.items():
    R=cv2.imread(png); O=cv2.imread(f'/workspace/medella_out/src_{side}.png')
    fit={r['id']:r for r in json.load(open(f'fit_{side}.json'))}; acc={}
    for l in lines(side):
        f=fit.get(l['id']); 
        if not f or f['size']*s*2.835<9: continue
        q=np.array(l['quad'],np.float32); x0,y0=q.min(0); x1,y1=q.max(0)
        o=O[int(y0):int(np.ceil(y1)),int(x0):int(np.ceil(x1))]
        qr=(np.array(off)+q*s)*k; X0,Y0=qr.min(0); X1,Y1=qr.max(0)
        r=R[int(Y0):int(np.ceil(Y1)),int(X0):int(np.ceil(X1))]
        light=f['cclass'].startswith('light')
        def core(a):
            g=cv2.cvtColor(a,cv2.COLOR_BGR2GRAY).astype(float)
            if light: bg=np.percentile(g,10); fg=np.percentile(g,98); m=g>fg-0.25*(fg-bg)
            else: bg=np.percentile(g,90); fg=np.percentile(g,2); m=g<fg+0.25*(bg-fg)
            return np.median(a.reshape(-1,3)[m.reshape(-1)],0)[::-1]
        acc.setdefault(f['cclass'],[]).append((core(o),core(r)))
    for kk,v in acc.items():
        print(side,kk,len(v),'orig core RGB',np.median([x[0] for x in v],0).round(),'render',np.median([x[1] for x in v],0).round())
