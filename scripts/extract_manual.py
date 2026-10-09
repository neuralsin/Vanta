import os
import sys
import xml.etree.ElementTree as ET
import zipfile

docx_path = r"d:\Vanta\Resolve Autopilot — Final Build Manual v2.docx"
output_path = r"d:\Vanta\docs\build_manual_raw.txt"

if not os.path.exists(docx_path):
    print("DOCX not found")
    sys.exit(1)

with zipfile.ZipFile(docx_path) as z:
    xml_content = z.read("word/document.xml")
    tree = ET.fromstring(xml_content)
    paragraphs = []
    for p in tree.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
        texts = [node.text for node in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t") if node.text]
        if texts:
            paragraphs.append("".join(texts))

with open(output_path, "w", encoding="utf-8") as f:
    f.write("\n\n".join(paragraphs))

print(f"Extracted {len(paragraphs)} paragraphs to {output_path}")
