"""Open synthetic fixtures; no organizer documents or expected findings in the UI."""
import io
import zipfile
from xml.sax.saxutils import escape


def document_bytes(lines, format='docx'):
    if format == 'docx':
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as archive:
            body = ''.join(f'<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>' for line in lines)
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+body+'</w:body></w:document>')
        return buf.getvalue()
    if format == 'xlsx':
        from openpyxl import Workbook
        book = Workbook()
        book.active.title = 'Функции'
        for line in lines:
            book.active.append([cell.strip() for cell in line.split('|')])
        buf = io.BytesIO()
        book.save(buf)
        return buf.getvalue()
    if format == 'pdf':
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=800, height=1000)
        # A small embedded Unicode map makes extraction deterministic across OSes.
        chars = list(dict.fromkeys(''.join(lines)))
        if len(chars)>250:
            raise ValueError('Synthetic PDF supports at most 250 distinct characters')
        codes = {char:index+1 for index,char in enumerate(chars)}
        cmap = DecodedStreamObject()
        mapping = '\n'.join(f'<{code:02X}> <{ord(char):04X}>' for char,code in codes.items())
        cmap.set_data((f'/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n1 begincodespacerange\n<00> <FF>\nendcodespacerange\n{len(chars)} beginbfchar\n{mapping}\nendbfchar\nendcmap end end').encode())
        font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica'),NameObject('/ToUnicode'):writer._add_object(cmap)})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(('BT /F1 10 Tf 30 950 Td 18 TL\n'+'\n'.join('<'+''.join(f'{codes[c]:02X}' for c in line)+'> Tj T*' for line in lines)+'\nET').encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
        buf = io.BytesIO(); writer.write(buf)
        return buf.getvalue()
    raise ValueError(format)


DEMO_BEFORE = ['1. Отдел закупок.', '2. Директор закупок:',
               '2.1. Контролирует качество закупочной деятельности.',
               '2.2. Формирует график обучения сотрудников.']
DEMO_AFTER = ['1. Отдел закупок.', '1.1. Отдел обучения.', '7. Директор контроля:',
              '7.1. Контролирует качество закупочной деятельности.',
              '8. Руководитель обучения:', '8.1. Формирует график обучения сотрудников.']
