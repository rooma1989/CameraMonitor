"""Pixel-exact monitor wall partitions; the first rectangle is the main view."""
import math


def _cells(x, y, width, height, columns, rows):
    return [(x + width*c//columns, y + height*r//rows,
             width*(c+1)//columns-width*c//columns,
             height*(r+1)//rows-height*r//rows)
            for r in range(rows) for c in range(columns)]


def wall_rectangles(count, width, height, featured=True, header=0, gap=0):
    if count <= 0:return []
    if count == 1:return [(0,0,width,height)]
    # Bottom columns align with the main view's vertical edge.
    presets = {6:(3,2,[2]), 9:(4,2,[2,2]), 10:(5,3,[2,2]),
               12:(5,2,[2,2,2]), 15:(4,2,[3,3])}
    if featured and count in (6,10,15):
        bottom_columns, main_columns, right_rows = presets[count]
        main_width = width*main_columns//bottom_columns
        bottom_rows=2 if count==15 else 1
        main_height = height*3//5 if count==15 else height*2//3
        result=[(0,0,main_width,main_height)]
        right_width=width-main_width
        for col, rows in enumerate(right_rows):
            x=main_width+right_width*col//len(right_rows)
            w=right_width*(col+1)//len(right_rows)-right_width*col//len(right_rows)
            result.extend(_cells(x,0,w,main_height,1,rows))
        result.extend(_cells(0,main_height,width,height-main_height,bottom_columns,bottom_rows))
        return result
    # Equal fixed slots, independent of connected camera count.
    columns, rows = {4:(2,2),9:(3,3),12:(4,3),16:(4,4),20:(5,4),25:(5,5)}.get(count,(math.ceil(math.sqrt(count)),math.ceil(count/math.ceil(math.sqrt(count)))))
    # Fit the entire fixed grid to the canvas. Empty slots retain their geometry.
    cell_width=max(1,min(width//columns, int(max(1,height/rows-header-gap)*16/9)+gap))
    cell_height=round(max(1,cell_width-gap)*9/16)+header+gap
    wall_width=cell_width*columns;wall_height=cell_height*rows
    return _cells((width-wall_width)//2,(height-wall_height)//2,
                  wall_width,wall_height,columns,rows)[:count]
