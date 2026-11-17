"""Administrative accounting views. Currencies are never silently mixed."""
import csv
import io
from datetime import date
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from .auth import require_admin, require_operator, csrf
from .db import transaction

router=APIRouter(prefix="/api/admin/finance")

class Entry(BaseModel):
    kind:str=Field(pattern="^(expense|fee|manual_sale)$")
    category:str=Field(default="other",pattern="^(server|domain|api|advertising|other)$")
    amount_minor:int=Field(gt=0,le=10**13)
    description:str=Field(min_length=3,max_length=500)
    reference:str|None=Field(default=None,max_length=100)

def _range(start:date|None,end:date|None):
    if start and end and end<start: raise HTTPException(422,"Invalid date range")
    return start or date(2000,1,1),end or date(9999,12,31)

@router.get("/report")
def report(start:date|None=None,end:date|None=None,provider:str|None=None,admin=Depends(require_admin)):
    start,end=_range(start,end)
    if provider and provider not in ("wallet","stars","nowpayments","manual"):
        raise HTTPException(422,"Invalid provider")
    with transaction() as conn:
        payments=conn.execute("""SELECT p.provider,p.currency,p.status,count(*) AS count,coalesce(sum(p.amount_minor),0) AS amount_minor
          FROM payments p WHERE p.created_at >= %s AND p.created_at < %s::date+interval '1 day' AND (%s::text IS NULL OR p.provider=%s)
          GROUP BY p.provider,p.currency,p.status ORDER BY p.provider,p.currency,p.status""",(start,end,provider,provider)).fetchall()
        entries=conn.execute("""SELECT kind,category,currency,count(*) AS count,coalesce(sum(amount_minor),0) AS amount_minor
          FROM finance_entries WHERE created_at >= %s AND created_at < %s::date+interval '1 day' AND reversed_by IS NULL
          GROUP BY kind,category,currency ORDER BY kind,category""",(start,end)).fetchall()
        refunds=conn.execute("""SELECT provider,currency,count(*) AS count,coalesce(sum(amount_minor),0) AS amount_minor
          FROM payments WHERE status='refunded' AND updated_at >= %s AND updated_at < %s::date+interval '1 day'
          AND (%s::text IS NULL OR provider=%s) GROUP BY provider,currency ORDER BY provider,currency""",(start,end,provider,provider)).fetchall()
    net={}
    for row in payments:
        if row["status"] in ("confirmed","refunded"):
            net[row["currency"]]=net.get(row["currency"],0)+row["amount_minor"]
    for row in refunds:
        net[row["currency"]]=net.get(row["currency"],0)-row["amount_minor"]
    for row in entries:
        direction={"manual_sale":1,"expense":-1,"fee":-1}[row["kind"]]
        net[row["currency"]]=net.get(row["currency"],0)+direction*row["amount_minor"]
    return {"payments":payments,"entries":entries,"refunds":refunds,"net_by_currency":net,"start":start,"end":end}

@router.get("/entries")
def list_entries(start:date|None=None,end:date|None=None,kind:str|None=None,admin=Depends(require_admin)):
    start,end=_range(start,end)
    if kind and kind not in ("expense","fee","manual_sale"):
        raise HTTPException(422,"Invalid kind")
    with transaction() as conn:
        return conn.execute("""SELECT f.id,f.kind,f.amount_minor,f.currency,f.description,f.reference,f.actor_id,
          f.created_at,f.reversed_by,f.category FROM finance_entries f WHERE f.created_at >= %s AND f.created_at < %s::date+interval '1 day'
          AND (%s::text IS NULL OR f.kind=%s) ORDER BY f.id DESC LIMIT 2000""",(start,end,kind,kind)).fetchall()

@router.post("/entries")
def add_entry(body:Entry,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        row=conn.execute("""INSERT INTO finance_entries(kind,category,amount_minor,description,reference,actor_id)
          VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (reference) WHERE reference IS NOT NULL DO NOTHING
          RETURNING id,kind,category,amount_minor,currency,description,reference,created_at""",
          (body.kind,body.category,body.amount_minor,body.description,body.reference,admin["id"])).fetchone()
        if not row: raise HTTPException(409,"Finance reference already recorded")
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id) VALUES (%s,'finance.entry.create','finance_entry',%s)",(admin["id"],row["id"]))
    return row

@router.post("/entries/{entry_id}/reverse")
def reverse_entry(entry_id:int,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        row=conn.execute("SELECT id,reversed_by FROM finance_entries WHERE id=%s FOR UPDATE",(entry_id,)).fetchone()
        if not row: raise HTTPException(404,"Entry not found")
        if row["reversed_by"]: return {"status":"reversed"}
        conn.execute("UPDATE finance_entries SET reversed_by=%s,reversed_at=now() WHERE id=%s",(admin["id"],entry_id))
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id) VALUES (%s,'finance.entry.reverse','finance_entry',%s)",(admin["id"],entry_id))
    return {"status":"reversed"}

def _safe_cell(value):
    value=str(value if value is not None else "")
    return "'"+value if value.startswith(("=","+","-","@","\t","\r")) else value

def _xml_text(value):
    value=str(value if value is not None else "")
    return escape("".join(char for char in value if ord(char) in (9,10,13) or ord(char)>=32))

def _rows(start,end):
    with transaction() as conn:
        payments=conn.execute("""SELECT 'payment' AS source,p.id,p.provider AS kind,'' AS category,'' AS description,p.amount_minor,p.currency,
          p.status,p.order_id::text AS reference,p.created_at FROM payments p
          WHERE p.created_at >= %s AND p.created_at < %s::date+interval '1 day' ORDER BY p.id LIMIT 10001""",(start,end)).fetchall()
        entries=conn.execute("""SELECT 'finance' AS source,id,kind,category,description,amount_minor,currency,
          CASE WHEN reversed_by IS NULL THEN 'recorded' ELSE 'reversed' END AS status,
          coalesce(reference,'') AS reference,created_at FROM finance_entries
          WHERE created_at >= %s AND created_at < %s::date+interval '1 day' ORDER BY id LIMIT 10001""",(start,end)).fetchall()
    if len(payments)+len(entries)>10000:
        raise HTTPException(413,"Export exceeds 10,000 rows; narrow the date range")
    return ["source","id","kind","category","description","amount_minor","currency","status","reference","created_at"],payments+entries

@router.get("/export")
def export(start:date|None=None,end:date|None=None,format:str="csv",admin=Depends(require_admin)):
    start,end=_range(start,end)
    if format not in ("csv","xlsx"): raise HTTPException(422,"Invalid export format")
    columns,rows=_rows(start,end)
    if format=="csv":
        output=io.StringIO()
        writer=csv.writer(output)
        writer.writerow(columns)
        writer.writerows([_safe_cell(row[key]) for key in columns] for row in rows)
        return Response("\ufeff"+output.getvalue(),media_type="text/csv; charset=utf-8",headers={"Content-Disposition":"attachment; filename=alart-finance.csv","Cache-Control":"no-store"})
    # Minimal standards-compliant spreadsheet with inline strings and no formulas.
    sheet=['<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
    for index,values in enumerate([columns,*([row[key] for key in columns] for row in rows)],1):
        sheet.append(f'<row r="{index}">')
        for cell in values:
            sheet.append(f'<c t="inlineStr"><is><t>{_xml_text(cell)}</t></is></c>')
        sheet.append('</row>')
    sheet.append('</sheetData></worksheet>')
    memory=io.BytesIO()
    with ZipFile(memory,"w",ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml",'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr("_rels/.rels",'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml",'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Finance" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels",'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml","".join(sheet))
    return Response(memory.getvalue(),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":"attachment; filename=alart-finance.xlsx","Cache-Control":"no-store"})
