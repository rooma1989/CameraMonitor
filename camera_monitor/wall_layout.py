"""Pixel-exact monitor wall partitions; the first rectangle is the main view."""
import math


def _cells(x, y, width, height, columns, rows):
    return [(x + width*c//columns, y + height*r//rows,
             width*(c+1)//columns-width*c//columns,
             height*(r+1)//rows-height*r//rows)
            for r in range(rows) for c in range(columns)]


def wall_rectangles(count, width, height, featured=True):
    if count <= 0:return []
    if count == 1:return [(0,0,width,height)]
    # Bottom columns align with the main view's vertical edge.
    presets = {6:(3,2,[2]), 9:(4,2,[2,2]), 10:(4,2,[2,3]),
               12:(4,2,[3,4]), 15:(4,2,[3,3])}
    if featured and count in presets:
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
    # Balanced rows fill the screen even at other device counts.
    columns=math.ceil(math.sqrt(count))
    rows=math.ceil(count/columns)
    result=[];remaining=count
    for row in range(rows):
        cells=math.ceil(remaining/(rows-row))
        y=height*row//rows;h=height*(row+1)//rows-y
        result.extend(_cells(0,y,width,h,cells,1));remaining-=cells
    return result
