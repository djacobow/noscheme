"""Write the Honest Clock's hand-drawn footprints into HonestClock.pretty.

    python3 make_footprints.py ../HonestClock.pretty

Each footprint is built from the named dimensions of its datasheet drawing,
so a measurement correction is a one-line change.

OLED_SSD1309_2.42in_Module: QDtech's MC242GX outline drawing (V1.0,
2023-08-11), lcdwiki.com/res/MC242GX/2.42inch_IIC_Module_MC242GX_Size.pdf.
That drawing shows the 5-pin I2C board; the 7-pin SPI board shares its
outline, holes and first five header positions (GND, VCC, SCL, SDA, RES) and
continues the header with DC and CS. Measure a real module before ordering
boards. The module mounts display-out on the front of this board, so the
drawing's front view is the view from the top of this board.

RJ45_HanRun_HY951180A_Vertical: HanRun HY951180A datasheet REV 00, page 3,
"Recommended PCB layout (Component Side View)". Dimensions there are measured
from the two 3.2 mm locating pegs.

USB_C_Receptacle_Hanxia_HX-TYPE-C-16P-180-LT-H10.0_Vertical: Hanxia drawing
"HX-TYPE-C 16P 180 LT H10.0", recommended P.C.B. layout (LCSC C49261569).
Its pad lengths follow from the 4.32 mm overall and 1.14 mm row gap; SHOU
HAN's TYPE-C 16P LTH10.0 143 shares the layout.
"""

import sys
from pathlib import Path

SILK, FAB, YARD = 0.12, 0.1, 0.05  # line widths
MARGIN = 0.25  # courtyard margin


class Footprint:
    def __init__(self, name, descr, tags, origin, attr="through_hole"):
        self.name, self.origin = name, origin
        self.lines = [
            f'(footprint "{name}"',
            "\t(version 20241229)",
            '\t(generator "noscheme_clock")',
            '\t(layer "F.Cu")',
            f'\t(descr "{descr}")',
            f'\t(tags "{tags}")',
        ]
        self.attr = attr

    def at(self, u, v):
        """Drawing coordinates to footprint coordinates."""
        return u - self.origin[0], v - self.origin[1]

    def property(self, kind, value, u, v, layer, hide=False):
        x, y = self.at(u, v)
        hidden = " (hide yes)" if hide else ""
        self.lines.append(
            f'\t(property "{kind}" "{value}" (at {x:.3f} {y:.3f} 0) (layer "{layer}"){hidden}\n'
            "\t\t(effects (font (size 1 1) (thickness 0.15))))"
        )

    def note(self, text, u, v, size=0.8, justify=""):
        x, y = self.at(u, v)
        just = f" (justify {justify})" if justify else ""
        self.lines.append(
            f'\t(fp_text user "{text}" (at {x:.3f} {y:.3f} 0) (layer "F.Fab")\n'
            f"\t\t(effects (font (size {size} {size}) (thickness 0.12)){just}))"
        )

    def rect(self, layer, left, top, width, height):
        stroke = {"F.SilkS": SILK, "F.Fab": FAB, "F.CrtYd": YARD}[layer]
        (x0, y0), (x1, y1) = self.at(left, top), self.at(left + width, top + height)
        self.lines.append(
            f"\t(fp_rect (start {x0:.3f} {y0:.3f}) (end {x1:.3f} {y1:.3f})\n"
            f'\t\t(stroke (width {stroke}) (type solid)) (fill no) (layer "{layer}"))'
        )

    def outline(self, left, top, width, height, overhang=0.0):
        """Silkscreen, fab and courtyard around a body; pads may overhang its sides."""
        self.rect("F.SilkS", left - 0.1, top - 0.1, width + 0.2, height + 0.2)
        self.rect("F.Fab", left, top, width, height)
        m = MARGIN + overhang
        self.rect("F.CrtYd", left - m, top - MARGIN, width + 2 * m, height + 2 * MARGIN)

    def pad(self, number, u, v, size, drill, shape="circle", function=None):
        x, y = self.at(u, v)
        func = f' (pinfunction "{function}")' if function else ""
        self.lines.append(
            f'\t(pad "{number}" thru_hole {shape} (at {x:.3f} {y:.3f}) (size {size} {size}) '
            f'(drill {drill})\n\t\t(layers "*.Cu" "*.Mask"){func})'
        )

    def smd(self, number, u, v, width, height):
        x, y = self.at(u, v)
        self.lines.append(
            f'\t(pad "{number}" smd roundrect (at {x:.3f} {y:.3f}) (size {width} {height}) '
            f'(roundrect_rratio 0.25)\n\t\t(layers "F.Cu" "F.Mask" "F.Paste"))'
        )

    def hole(self, u, v, drill):
        x, y = self.at(u, v)
        self.lines.append(
            f'\t(pad "" np_thru_hole circle (at {x:.3f} {y:.3f}) (size {drill} {drill}) '
            f'(drill {drill})\n\t\t(layers "*.Cu" "*.Mask"))'
        )

    def text(self):
        attr = f"\t(attr {self.attr})"
        return "\n".join([*self.lines[:6], attr, *self.lines[6:], ")"]) + "\n"


def oled():
    pcb = (72.0, 43.0)  # module board outline
    holes = [(2.0, 2.0), (70.0, 2.0), (2.0, 41.0), (70.0, 41.0)]  # module holes are 2.5 mm
    drill = 2.2  # M2 clearance through this board
    pin1 = (2.50, 25.31)  # 15.15 to pin 5 plus 4 x 2.54
    pitch = 2.54
    pins = ["GND", "VCC", "SCL", "SDA", "RES", "DC", "CS"]  # pin 1 at the bottom
    frame = (4.95, 2.10, 62.10, 38.80)  # iron frame: left, top, width, height
    panel = (5.75, 3.00, 60.50, 37.00)
    active = (8.50, 5.07, 55.01, 27.49)

    fp = Footprint(
        "OLED_SSD1309_2.42in_Module",
        "2.42 inch 128x64 SSD1309 OLED module, 7-pin 2.54 mm SPI header, "
        "QDtech MC242GX outline: 72 x 43 mm board, holes 68 x 39 mm",
        "OLED SSD1309 2.42 module",
        pin1,
    )
    fp.property("Reference", "REF**", 0, -1.2, "F.SilkS")
    fp.property("Value", fp.name, pcb[0] / 2, pcb[1] / 2, "F.Fab")
    fp.property(
        "Datasheet",
        "https://www.lcdwiki.com/2.42inch_IIC_OLED_Module_SKU:MC242GX",
        *pin1,
        "F.Fab",
        hide=True,
    )
    fp.outline(0, 0, *pcb)
    for box in (frame, panel, active):
        fp.rect("F.Fab", *box)
    for n, name in enumerate(pins):
        v = pin1[1] - n * pitch
        fp.pad(n + 1, pin1[0], v, 1.7, 1.0, "rect" if n == 0 else "circle", name)
        fp.note(name, pin1[0] + 2.2, v, justify="left")
    for u, v in holes:
        fp.hole(u, v, drill)
    return fp


def rj45():
    # Drawing frame: x from the centreline, y down from the locating pegs.
    body = (16.20, 17.00, 6.83)  # width, depth, pegs below the top edge
    odd_row, even_row = 8.89 - 2.54, 8.89  # pins 1, 3, 5, 7 and 2, 4, 6, 8
    pitch = 1.27
    leds = {"12": -6.325, "11": -3.785, "10": 3.785, "9": 6.325}  # 12.65 and 7.57 apart
    led_row = -4.11
    pegs = 11.43 / 2  # 3.2 mm plastic pegs
    shield, shield_row = 15.80 / 2, 3.89  # 1.63 mm shield tabs
    names = ["TD+", "TD-", "TCT", "NC", "RCT", "RD+", "RD-", "CHS_GND"]

    def pin_x(n):  # pin 8 at the left, alternating rows
        return (n - 4.5) * -pitch

    fp = Footprint(
        "RJ45_HanRun_HY951180A_Vertical",
        "HanRun HY951180A 1x1 vertical (tab-up) RJ45 with magnetics and LEDs, 10/100Base-TX",
        "RJ45 8P8C vertical magjack HY951180A",
        (pin_x(1), odd_row),
    )
    top = -body[2]
    fp.property("Reference", "REF**", 0, top - 1.2, "F.SilkS")
    fp.property("Value", fp.name, 0, 0, "F.Fab")
    fp.property(
        "Datasheet",
        "https://www.lcsc.com/datasheet/C34677.pdf",
        pin_x(1),
        odd_row,
        "F.Fab",
        hide=True,
    )
    fp.outline(-body[0] / 2, top, body[0], body[1], overhang=shield + 1.2 - body[0] / 2)
    fp.note("LEDs", 0, top + 1.5)
    for n in range(1, 9):
        fp.pad(
            n,
            pin_x(n),
            odd_row if n % 2 else even_row,
            1.4,
            0.9,
            "rect" if n == 1 else "circle",
            names[n - 1],
        )
    for number, u in leds.items():
        fp.pad(number, u, led_row, 1.6, 1.0)
    for side in (-1, 1):
        fp.hole(side * pegs, 0, 3.2)
        fp.pad("SH", side * shield, shield_row, 2.4, 1.65)
    return fp


def usb_c():
    # Drawing frame: origin at the centre of the land pattern, B row on top.
    pitch, pad = 1.00, (0.50, (4.32 - 1.14) / 2)
    row = (1.14 + pad[1]) / 2  # row centres from the middle
    b_row = ["B1", "B4", "B5", "B6", "B7", "B8", "B9", "B12"]  # left to right
    a_row = ["A12", "A9", "A8", "A7", "A6", "A5", "A4", "A1"]
    # Shell tabs, SMD, under legs 8.45 apart. The drawing's 1.20 mm tabs end
    # 0.135 mm from the peg holes; trim 0.12 from the outer ends to meet the
    # 0.25 mm hole clearance.
    trim = 0.12
    tab, tab_x, tab_y = (1.20 - trim, 0.45), 8.45 / 2 - trim / 2, 2.96 / 2
    pegs = [(-10.82 / 2, -3.00 / 2), (10.82 / 2, 3.00 / 2)]  # 0.70 mm plastic posts
    body = (12.00, 5.00)  # bottom-view outline

    fp = Footprint(
        "USB_C_Receptacle_Hanxia_HX-TYPE-C-16P-180-LT-H10.0_Vertical",
        "USB 2.0 Type-C receptacle, 16 pins, vertical (180 degree), 10 mm tall, SMD, "
        "Hanxia HX-TYPE-C 16P 180 LT H10.0",
        "USB C Type-C receptacle vertical USB2.0 16P",
        (0, 0),
        attr="smd",
    )
    fp.property("Reference", "REF**", 0, -body[1] / 2 - 1.2, "F.SilkS")
    fp.property("Value", fp.name, 0, body[1] / 2 + 1.2, "F.Fab")
    fp.property(
        "Datasheet", "https://www.lcsc.com/datasheet/C49261569.pdf", 0, 0, "F.Fab", hide=True
    )
    fp.outline(-body[0] / 2, -body[1] / 2, *body)
    for i, (b, a) in enumerate(zip(b_row, a_row)):
        u = (i - 3.5) * pitch
        fp.smd(b, u, -row, *pad)
        fp.smd(a, u, row, *pad)
    for side in (-1, 1):
        for v in (-tab_y, tab_y):
            fp.smd("SH", side * tab_x, v, *tab)
    for u, v in pegs:
        fp.hole(u, v, 0.90)
    return fp


if __name__ == "__main__":
    out = Path(sys.argv[1])
    for fp in (oled(), rj45(), usb_c()):
        path = out / f"{fp.name}.kicad_mod"
        path.write_text(fp.text())
        print("wrote", path)
