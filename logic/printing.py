"""Send a file straight to the default printer, no manual "open then print"
step for the cashier. Uses Windows' own shell print verb, which hands the
file to whatever's registered to open PDFs (Edge, Adobe Reader, etc.) and
tells it to print -- reliable and needs no extra dependency."""
import os
import sys


def print_document(path) -> bool:
    """Best-effort direct print. Returns True if the OS accepted the direct
    "print" hand-off (not a guarantee ink hit paper -- that's between the OS
    and the printer). Never raises.

    Some shop PCs have no application registered for the PDF "print" shell
    verb (e.g. only a browser that can view but not silently print a PDF),
    so the direct hand-off can fail with WinError 1155 ("No application is
    associated..."). A False return doesn't just leave the cashier guessing
    where the receipt went: this still opens the file normally (the "open"
    verb, which is registered on virtually every Windows PC) so they can
    print it by hand with Ctrl+P."""
    if sys.platform != "win32":
        return False
    try:
        os.startfile(str(path), "print")
        return True
    except Exception:
        try:
            os.startfile(str(path))
        except Exception:
            pass
        return False
