import json,sys,numpy as np,cv2
sys.path.insert(0,'.')
from spec import lines
from groups import group
SIDE={'flyer_front':(205/1536,((158-1024*205/1536)/2,7.5),'q_fly-1.png'),'flyer_back':(148/1054,(5,(220-1492*148/1054)/2),'q_fly-2.png'),'card_front':(50/992,((100-1586*50/992)/2,5),'q_card-1.png')}
k=300/25.4
out={}
for side,(s,off,png) in SIDE.items():
    R=cv2.cvtColor(cv2.imread(png),cv2.COLOR_BGR2GRAY).astype(np.float32)
    O=cv2.cvtColor(cv2.imread(f'/workspace/medella_out/src_{side}.png'),cv2.COLOR_BGR2GRAY).astype(np.float32)
    fit={r['id']:r for r in json.load(open(f'fit_{side}.json'))}
    res={}
    for l in lines(side):
        q=np.array(l['quad'],np.float32); x0,y0=q.min(0); x1,y1=q.max(0)
        o=O[int(y0):int(np.ceil(y1)),int(x0):int(np.ceil(x1))]
        qr=(np.array(off)+q*s)*k; X0,Y0=qr.min(0); X1,Y1=qr.max(0)
        r=R[int(Y0):int(np.ceil(Y1)),int(X0):int(np.ceil(X1))]
        r=cv2.resize(r,(o.shape[1],o.shape[0]),interpolation=cv2.INTER_AREA)
        def ink(a):
            bg=np.percentile(a,90); fg=np.percentile(a,3)
            if bg-fg<40: return None
            return np.clip((bg-a)/(bg-fg+1e-6),0,1).sum()
        io,ir=ink(o),ink(r)
        if io and ir: res.setdefault((group(l['text']),fit.get(l['id'],{}).get('cclass','?')),[]).append(ir/io)
    for kk,v in sorted(res.items()): print(side,kk,'n',len(v),'render/orig ink median',round(float(np.median(v)),3))
