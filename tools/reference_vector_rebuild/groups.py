SCRIPT={'The Surface','See your health,','live your best life','Live Better.','Chandré','See your health, live your best life',
        'By appointment only','A wide range of conditions','Your health is my priority.','Because you deserve to','feel your best, every day.','When you know better,','you can do better.','Your health journey','starts with knowledge.'}
ITALIC={'Empowering you to take charge of your health.'}
def group(text):
    t=text.strip()
    if t in SCRIPT: return 'script'
    if t in ITALIC: return 'italic'
    letters=[c for c in t if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and len(letters)>=2: return 'caps'
    return 'body'
CANDS={'caps':['Cinzel 400','Cinzel 500','Cinzel 600','Cinzel 700','Cormorant SC 600','Cormorant SC 700','Marcellus','Libre Baskerville 400','Libre Baskerville 700','Playfair Display 500','EB Garamond 500','EB Garamond 600','Crimson Text 600','Cormorant Garamond 600','Cormorant Garamond 700'],
       'body':['Libre Baskerville 400','EB Garamond 400','EB Garamond 500','Cormorant Garamond 500','Cormorant Garamond 600','Crimson Text 400','Lora 400','Playfair Display 400','Cormorant 500','Trirong 400'],
       'script':['Great Vibes','Allura','Pinyon Script','Parisienne','Alex Brush','Tangerine Bold'],
       'italic':['Cormorant Garamond Italic 500','Cormorant Garamond Italic 600','EB Garamond Italic 400','Libre Baskerville Italic 400','Playfair Display Italic 400','Crimson Text Italic','Lora Italic 400']}
