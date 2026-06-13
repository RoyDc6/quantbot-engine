import sys
try:
    import easyocr
    print("easyocr imported OK", flush=True)
    reader = easyocr.Reader(['ch_sim', 'en'], gpu=False)
    print("reader created", flush=True)
    results = reader.readtext(r'E:\quant\research\error_screenshot.png')
    for bbox, text, conf in results:
        print(f'[{conf:.2f}] {text}', flush=True)
except Exception as e:
    print(f"ERROR: {e}", flush=True)
    import traceback
    traceback.print_exc()
    sys.exit(1)