import xml.etree.ElementTree as ET, sys
from PIL import Image, ImageDraw, ImageFont
lib=sys.argv[1]; r=ET.parse(f'{lib}/{lib}.dspreset').getroot(); tab=r.find('ui/tab')
F=lambda s: ImageFont.truetype('/usr/share/fonts/opentype/inter/Inter-Medium.otf',s)
def render(deep):
    bg=Image.open(f'{lib}/Resources/panel.png').convert('RGB'); im=Image.new('RGB',bg.size,'#141518'); im.paste(bg,(0,-50)); d=ImageDraw.Draw(im)
    for e in tab:
        tags=(e.get('tags') or '').split(','); v=e.get('visible','true')=='true'
        if 'main' in tags: v=not deep
        if 'deep' in tags: v=deep
        if 'pk' in tags: v=deep and 'pk00' in tags
        if not v: continue
        x,y,w,h=[int(float(e.get(k,0))) for k in ('x','y','width','height')]
        col='#'+(e.get('textColor','FFFFFFFF')[2:])
        if e.tag=='label':
            ty = y if e.get('vAlign')=='top' else (y+h-14 if e.get('vAlign')=='bottom' else y+(h-int(e.get('textSize',12)))//2)
            s=int(e.get('textSize',12)); txt=e.get('text')
            if e.get('hAlign')=='left': d.text((x,ty),txt,font=F(s-1),fill=col)
            else: d.text((x+w//2,ty),txt,font=F(s-1),fill=col,anchor='ma')
        elif e.tag=='labeled-knob':
            d.text((x+w//2,y+2),e.get('label'),font=F(12),fill=col,anchor='ma')
            cx,cy,rad=x+w//2,y+h//2+8,min(w,h)//2-16
            lo,hi,val=float(e.get('minValue')),float(e.get('maxValue')),float(e.get('value'))
            d.arc([cx-rad,cy-rad,cx+rad,cy+rad],135,405,fill='#3A3B40',width=4)
            d.arc([cx-rad,cy-rad,cx+rad,cy+rad],135,135+270*(val-lo)/(hi-lo),fill='#E8553D',width=4)
        elif e.tag=='menu':
            d.rectangle([x,y,x+w,y+h],fill='#232428',outline='#444'); o=e.findall('option')[int(e.get('value'))-1]
            d.text((x+8,y+h//2),o.get('name'),font=F(12),fill='#E6E1D8',anchor='lm')
        elif e.tag=='button':
            d.rectangle([x,y,x+w,y+h],fill='#E8553D' if deep else '#2C2D32')
            d.text((x+w//2,y+h//2),e.findall('state')[1 if deep else 0].get('name'),font=F(12),fill='white',anchor='mm')
        elif e.tag=='rectangle':
            d.rectangle([x,y,x+w,y+h],fill='#'+e.get('fillColor')[2:],outline='#'+e.get('borderColor')[2:])
    return im
a,b=render(False),render(True); c=Image.new('RGB',(812,760),'black'); c.paste(a,(0,0)); c.paste(b,(0,385)); c.save(sys.argv[2])
