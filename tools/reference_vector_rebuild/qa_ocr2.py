import json,sys,numpy as np,unicodedata,difflib,cv2
from PIL import Image
Image.MAX_IMAGE_PIXELS=None
from rapidocr import RapidOCR
import pymupdf
sys.path.insert(0,'/workspace/medwork')
from spec import lines
eng=RapidOCR()
MM=25.4
SIDE={'flyer_front':(205/1536,((158-1024*205/1536)/2,7.5),'q_fly-1.png','flyer_A5_front_back_press.pdf',0),
      'flyer_back':(148/1054,(5,(220-1492*148/1054)/2),'q_fly-2.png','flyer_A5_front_back_press.pdf',1),
      'card_front':(50/992,((100-1586*50/992)/2,5),'q_card-1.png','business_card_front_back_press.pdf',0)}
SRC={'flyer_front':'/workspace/medella_out/src_flyer_front.png','flyer_back':'/workspace/medella_out/src_flyer_back.png','card_front':'/workspace/medella_out/src_card_front.png'}
def norm(t): return unicodedata.normalize('NFC',t.replace('\u2019',"'").replace('\u2013','-')).strip()
def sq(t): return norm(t).replace(' ','').replace('Ｅ','E')
def quadcrop(img,q,padf=0.22,out_h=96):
    q=np.array(q,np.float32); h=(np.linalg.norm(q[3]-q[0])+np.linalg.norm(q[2]-q[1]))/2; w=(np.linalg.norm(q[1]-q[0])+np.linalg.norm(q[2]-q[3]))/2
    ux=(q[1]-q[0])/np.linalg.norm(q[1]-q[0]); uy=(q[3]-q[0])/np.linalg.norm(q[3]-q[0])
    p=h*padf; px=max(p,h*0.5)
    src=np.array([q[0]-ux*px-uy*p,q[1]+ux*px-uy*p,q[2]+ux*px+uy*p,q[3]-ux*px+uy*p],np.float32)
    S=out_h/(h+2*p); W=int((w+2*px)*S); H=int(out_h)
    M=cv2.getPerspectiveTransform(src,np.array([[0,0],[W,0],[W,H],[0,H]],np.float32))
    c=cv2.warpPerspective(img,M,(W,H),flags=cv2.INTER_CUBIC,borderMode=cv2.BORDER_REPLICATE)
    c=cv2.copyMakeBorder(c,24,24,24,24,cv2.BORDER_REPLICATE)
    return c
def rec(c):
    r=eng(c)
    if r.txts is None: return ''
    items=sorted(zip(r.boxes,r.txts),key=lambda z:(np.array(z[0])[:,0].min()))
    return ' '.join(t for _,t in items)
def pdf_spans(pdf,page):
    d=pymupdf.open('/workspace/medella_vector/'+pdf); p=d[page]
    return [norm(''.join(s['text'] for s in l['spans'])) for b in p.get_text('dict')['blocks'] for l in b.get('lines',[])]
rep={}
for side,(s,off,png,pdf,pg) in SIDE.items():
    k=300/MM
    R=cv2.imread(png); O=cv2.imread(SRC[side])
    spans=[sq(x) for x in pdf_spans(pdf,pg)]
    res=[]
    for l in lines(side):
        exp=norm(l['text']); q=np.array(l['quad'],float)
        qr=(np.array(off)+q*s)*k
        gr=rec(quadcrop(R,qr)); go=rec(quadcrop(O,q))
        ok_r=sq(gr)==sq(exp); ok_o=sq(go)==sq(exp); agree=sq(gr)==sq(go)
        res.append(dict(id=l['id'],expected=exp,ocr_render=gr,ocr_original=go,render_eq_expected=ok_r,original_eq_expected=ok_o,render_eq_original=agree,
                        pdf_text=sq(exp) in spans))
    rep[side]=res
    n=len(res)
    print(f'== {side}: {n} lines | pdf text exact {sum(r["pdf_text"] for r in res)}/{n} | render-OCR==expected {sum(r["render_eq_expected"] for r in res)}/{n} | render-OCR==original-OCR {sum(r["render_eq_original"] for r in res)}/{n}')
    for r in res:
        if not r['pdf_text'] or not (r['render_eq_expected'] or r['render_eq_original']):
            print('   ',r['id'],'| exp:',r['expected'],'| render:',r['ocr_render'],'| orig:',r['ocr_original'],'| pdf' if r['pdf_text'] else '| NOT-IN-PDF')
json.dump(rep,open('qa_ocr2.json','w'),indent=1,ensure_ascii=False)
