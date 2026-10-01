import glob, os
D='/workspace/medfonts/static'
def pool():
    P=[]
    def add(lbl,f,kind):
        p=os.path.join(D,f)
        if os.path.exists(p): P.append((lbl,p,kind))
    for w in (400,500,600,700):
        add(f'Cinzel {w}',f'Cinzel-w{w}.ttf','caps')
        add(f'Cormorant Garamond {w}',f'CormorantGaramond-w{w}.ttf','serif')
        add(f'Cormorant {w}',f'Cormorant-w{w}.ttf','serif')
        add(f'EB Garamond {w}',f'EBGaramond-w{w}.ttf','serif')
        add(f'Libre Baskerville {w}',f'LibreBaskerville-w{w}.ttf','serif')
        add(f'Playfair Display {w}',f'PlayfairDisplay-w{w}.ttf','serif')
        add(f'Lora {w}',f'Lora-w{w}.ttf','serif')
        add(f'Cormorant Garamond Italic {w}',f'CormorantGaramond-Italic-w{w}.ttf','italic')
        add(f'EB Garamond Italic {w}',f'EBGaramond-Italic-w{w}.ttf','italic')
        add(f'Libre Baskerville Italic {w}',f'LibreBaskerville-Italic-w{w}.ttf','italic')
        add(f'Playfair Display Italic {w}',f'PlayfairDisplay-Italic-w{w}.ttf','italic')
        add(f'Lora Italic {w}',f'Lora-Italic-w{w}.ttf','italic')
    for f,l in [('CormorantSC-Regular.ttf','Cormorant SC 400'),('CormorantSC-Medium.ttf','Cormorant SC 500'),('CormorantSC-SemiBold.ttf','Cormorant SC 600'),('CormorantSC-Bold.ttf','Cormorant SC 700'),
                ('Marcellus-Regular.ttf','Marcellus'),('MarcellusSC-Regular.ttf','Marcellus SC'),('Trirong-Regular.ttf','Trirong 400'),
                ('CrimsonText-Regular.ttf','Crimson Text 400'),('CrimsonText-SemiBold.ttf','Crimson Text 600')]:
        add(l,f,'caps' if 'SC' in l or 'Marcellus' in l else 'serif')
    add('Crimson Text Italic','CrimsonText-Italic.ttf','italic')
    for f,l in [('GreatVibes-Regular.ttf','Great Vibes'),('Allura-Regular.ttf','Allura'),('PinyonScript-Regular.ttf','Pinyon Script'),
                ('Parisienne-Regular.ttf','Parisienne'),('AlexBrush-Regular.ttf','Alex Brush'),('Tangerine-Bold.ttf','Tangerine Bold')]:
        add(l,f,'script')
    return P
if __name__=='__main__':
    P=pool(); print(len(P)); print(sorted(set(k for _,_,k in P)))
