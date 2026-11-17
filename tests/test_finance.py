import io
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree
from fastapi import HTTPException
from alart.finance_routes import _safe_cell, _xml_text, _range, export, report, list_entries


class FinanceTests(unittest.TestCase):
    def test_csv_formula_neutralization(self):
        self.assertEqual(_safe_cell("=HYPERLINK(1)"),"'=HYPERLINK(1)")
        self.assertEqual(_safe_cell("normal"),"normal")

    def test_xml_omits_invalid_control_characters(self):
        self.assertEqual(_xml_text("A\x01&B"),"A&amp;B")

    def test_rejects_inverted_date_range(self):
        from datetime import date
        with self.assertRaises(HTTPException): _range(date(2026,9,18),date(2026,9,17))

    def test_excel_export_is_valid_spreadsheet_archive(self):
        rows=[{"source":"finance","id":1,"kind":"expense","amount_minor":1000,"currency":"IRR",
               "status":"recorded","reference":"ref-1","created_at":"2026-09-17"}]
        with patch("alart.finance_routes._rows",return_value=(["source","id","kind","amount_minor","currency","status","reference","created_at"],rows)):
            response=export(format="xlsx",admin={"id":1})
        with ZipFile(io.BytesIO(response.body)) as archive:
            self.assertIn("xl/worksheets/sheet1.xml",archive.namelist())
            sheet=ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        self.assertEqual(len(sheet),1)

    def test_refunded_payment_is_counted_once_in_net_flow(self):
        from contextlib import contextmanager
        class Cursor:
            def __init__(self,rows): self.rows=rows
            def fetchall(self): return self.rows
        class Connection:
            def __init__(self): self.index=0
            def execute(self,sql,params):
                values=[
                    [{"provider":"stars","currency":"XTR","status":"refunded","count":1,"amount_minor":100}],
                    [],
                    [{"provider":"stars","currency":"XTR","count":1,"amount_minor":100}],
                ][self.index]
                self.index+=1
                return Cursor(values)
        @contextmanager
        def database(): yield Connection()
        with patch("alart.finance_routes.transaction",database):
            result=report(admin={"id":1})
        self.assertEqual(result["net_by_currency"]["XTR"],0)

    def test_optional_filters_are_cast_for_postgresql_null_parameters(self):
        from contextlib import contextmanager
        class Cursor:
            def fetchall(self): return []
        class Connection:
            def __init__(self): self.statements=[]
            def execute(self,sql,params): self.statements.append(sql); return Cursor()
        connection=Connection()
        @contextmanager
        def database(): yield connection
        with patch('alart.finance_routes.transaction',database):
            report(admin={'id':1})
            list_entries(admin={'id':1})
        self.assertEqual(sum('%s::text IS NULL' in sql for sql in connection.statements),3)


if __name__=="__main__": unittest.main()
