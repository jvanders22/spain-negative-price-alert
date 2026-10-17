"""Site-wide settings that are not secrets.

GOATCOUNTER_CODE: the site code from https://www.goatcounter.com/signup (the "xyz" in
xyz.goatcounter.com). Empty means no analytics tag is rendered anywhere.
"""

GOATCOUNTER_CODE = ""


def goatcounter_tag(code: str | None = None) -> str:
    code = GOATCOUNTER_CODE if code is None else code
    if not code:
        return ""
    return (
        f'<script data-goatcounter="https://{code}.goatcounter.com/count" '
        'async src="//gc.zgo.at/count.js"></script>\n'
    )
