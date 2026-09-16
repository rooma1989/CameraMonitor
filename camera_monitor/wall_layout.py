"""Fit a wall of 16:9 tiles into the available canvas, leaving only outer margins."""
import math


def _template(count, featured):
    if count == 1:
        return [(0, 0, 1)]
    if featured and count == 6:
        return [(0,0,2),(2,0,1),(2,1,1)]+[(x,2,1) for x in range(3)]
    if featured and count == 10:
        return [(0,0,4)]+[(4,y,1) for y in range(4)]+[(x,4,1) for x in range(5)]
    if featured and count == 15:
        return ([(0,0,2)]+[(2+x*2/3,y*2/3,2/3) for x in range(2) for y in range(3)]
                +[(x*5/6,2+y*5/6,5/6) for y in range(2) for x in range(4)])
    columns,rows={4:(2,2),9:(3,3),12:(4,3),16:(4,4),20:(5,4),25:(5,5)}.get(
        count,(math.ceil(math.sqrt(count)),math.ceil(count/math.ceil(math.sqrt(count)))))
    return [(x,y,1) for y in range(rows) for x in range(columns)][:count]


def wall_rectangles(count,width,height,featured=True,header=0,gap=0):
    # Titles are overlays, so the whole tile (including empty slots) is 16:9.
    if count<=0:return []
    cells=_template(count,featured)
    columns=max(x+size for x,y,size in cells)
    rows=max(y+size for x,y,size in cells)
    scale=min(width/(columns*16),height/(rows*9))
    left=(width-columns*16*scale)/2
    top=(height-rows*9*scale)/2
    result=[]
    for x,y,size in cells:
        x1=round(left+x*16*scale);y1=round(top+y*9*scale)
        x2=round(left+(x+size)*16*scale);y2=round(top+(y+size)*9*scale)
        result.append((x1,y1,max(1,x2-x1),max(1,y2-y1)))
    return result
