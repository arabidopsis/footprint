import math
import os


def human(num, suffix="B", scale=1):
    if not num:
        return ""
    num *= scale
    magnitude = int(math.floor(math.log(abs(num), 1000)))
    val = num / math.pow(1000, magnitude)
    if magnitude > 7:
        return "{:.1f}{}{}".format(val, "Y", suffix)
    return "{:3.1f}{}{}".format(
        val, ["", "k", "M", "G", "T", "P", "E", "Z"][magnitude], suffix
    )


def rmfiles(files):
    for f in files:
        try:
            os.remove(f)
        except OSError:
            pass
