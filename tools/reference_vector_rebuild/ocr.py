import json,sys,numpy as np
from PIL import Image
from rapidocr import RapidOCR
eng=RapidOCR()
out={}
for side in ['flyer_front','flyer_back','card_front','card_back']:
    im=Image.open(f'/workspace/medella_out/src_{side}.png').convert('RGB')
    S=2
    big=im.resize((im.width*S,im.height*S),Image.LANCZOS)
    r=eng(np.array(big))
    lines=[]
    for box,txt,sc in zip(r.boxes,r.txts,r.scores):
        b=np.array(box)/S
        lines.append({'quad':[[round(float(x),1),round(float(y),1)] for x,y in b],'text':txt,'score':round(float(sc),3),'box':[round(float(b[:,0].min()),1),round(float(b[:,1].min()),1),round(float(b[:,0].max()),1),round(float(b[:,1].max()),1)]})
    out[side]={'size':im.size,'lines':lines}
    print(side,len(lines))
json.dump(out,open('ocr.json','w'),indent=1,ensure_ascii=False)
