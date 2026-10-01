import json,sys,numpy as np,unicodedata,difflib
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
CARD_BACK=['LIVE BLOOD ANALYSIS','A wide range of conditions','IMMUNE','SUPPORT','ENERGY &','FATIGUE','SKIN','CONDITIONS','DIGESTIVE','HEALTH','HORMONE','BALANCE','DETECT','BALANCE','HEAL','LIVE BETTER']
def norm(t): return unicodedata.normalize('NFC',t.replace('\u2019',"'").replace('\u2013','-').replace('–','-')).strip()
def ocr(path,scale=1.0):
    im=Image.open(path).convert('RGB')
    if scale!=1.0: im=im.resize((int(im.width*scale),int(im.height*scale)),Image.LANCZOS)
    r=eng(np.array(im)); out=[]
    for b,t,s in zip(r.boxes,r.txts,r.scores):
        b=np.array(b)/scale; out.append(dict(box=[b[:,0].min(),b[:,1].min(),b[:,0].max(),b[:,1].max()],text=t,score=float(s)))
    return out
def pdf_lines(pdf,page):
    d=pymupdf.open('/workspace/medella_vector/'+pdf); p=d[page]
    spans=[]
    for b in p.get_text('dict')['blocks']:
        for l in b.get('lines',[]):
            t=''.join(s['text'] for s in l['spans']); spans.append(norm(t))
    return spans
rep={}
for side,(s,off,png,pdf,pg) in SIDE.items():
    dpi=300; k=dpi/MM
    O=ocr(png, 1.0 if 'card' not in side else 2.0)
    L=lines(side)
    pdfspans=pdf_lines(pdf,pg)
    res=[]
    for l in L:
        exp=norm(l['text']); b=l['box']
        X0=(off[0]+b[0]*s)*k; Y0=(off[1]+b[1]*s)*k; X1=(off[0]+b[2]*s)*k; Y1=(off[1]+b[3]*s)*k
        pad=(Y1-Y0)*0.25
        hits=[o for o in O if X0-pad<= (o['box'][0]+o['box'][2])/2 <=X1+pad and Y0-pad<=(o['box'][1]+o['box'][3])/2<=Y1+pad]
        hits.sort(key=lambda o:o['box'][0])
        got=norm(' '.join(o['text'] for o in hits))
        ratio=difflib.SequenceMatcher(None,exp.replace(' ',''),got.replace(' ','')).ratio()
        inpdf=exp in pdfspans or any(exp==sp for sp in pdfspans)
        res.append(dict(id=l['id'],expected=exp,ocr=got,ratio=round(ratio,3),in_pdf_text=inpdf))
    rep[side]=res
# card back
O=ocr('q_card-2.png',2.0); spans=pdf_lines('business_card_front_back_press.pdf',1)
allocr=' | '.join(o['text'] for o in O)
rep['card_back']=[dict(expected=e,in_pdf_text=e in spans, ocr_found=norm(e).replace(' ','') in norm(allocr).replace(' ','')) for e in CARD_BACK]
rep['card_back_ocr_raw']=allocr
json.dump(rep,open('qa_ocr.json','w'),indent=1,ensure_ascii=False)
for side in SIDE:
    R=rep[side]; bad=[r for r in R if r['ratio']<1.0 or not r['in_pdf_text']]
    print(f'== {side}: {len(R)} lines, exact OCR {sum(r["ratio"]==1.0 for r in R)}, pdf-text exact {sum(r["in_pdf_text"] for r in R)}')
    for r in bad: print('  ',r['id'],'|',r['expected'],'| OCR:',r['ocr'],'|',r['ratio'],'pdf' if r['in_pdf_text'] else 'NOT-IN-PDF')
print('== card_back'); [print('  ',r) for r in rep['card_back']]; print(allocr)
