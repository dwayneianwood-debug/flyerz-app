import sys,cv2,numpy as np
sys.argv  # side x0 y0 x1 y1 out scale
from pdf import SIDE
side,x0,y0,x1,y1,out=sys.argv[1],*map(float,sys.argv[2:6]),sys.argv[6]
sc=float(sys.argv[7]) if len(sys.argv)>7 else 3
q={'flyer_front':'q_fly-1.png','flyer_back':'q_fly-2.png','card_front':'q_card-1.png','card_back':'q_card-2.png'}[side]
R=cv2.imread(q); S=SIDE[side]; pw,ph=S['page']; k=R.shape[1]/pw
src=cv2.imread(f'/workspace/medella_out/src_{side}.png')
a=src[int(y0):int(y1),int(x0):int(x1)]
X0,Y0,X1,Y1=[int((S['off'][i%2]+v*S['s'])*k) for i,v in enumerate((x0,y0,x1,y1))]
b=R[Y0:Y1,X0:X1]
w=int((x1-x0)*sc); h=int((y1-y0)*sc)
a=cv2.resize(a,(w,h),interpolation=cv2.INTER_CUBIC); b=cv2.resize(b,(w,h),interpolation=cv2.INTER_AREA)
sep=np.zeros((6,w,3),np.uint8); sep[...,2]=255
cv2.imwrite(out,np.vstack([a,sep,b]))
