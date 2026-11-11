"""Package PDF contract tests without AWS, fonts, or PDF-layer dependencies."""
import ast, json, unittest
from pathlib import Path
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'lambdas/tp-render-pdf/lambda_function.py').read_text()
tree=ast.parse(source)
helper=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_agency_options')
scope={'Decimal':Decimal,'_t':str,'_FAMILY':'Helvetica'}
exec(compile(ast.Module(body=[helper],type_ignores=[]),'pdf-helper','exec'),scope)
class PDF:
    def __init__(self): self.lines=[];self.pages=0
    def add_page(self):self.pages+=1
    def set_font(self,*args):pass
    def ln(self,*args):pass
    def multi_cell(self,w,h,text,**kw):self.lines.append(text)
ROW={'title':'Tokyo Culture and Food','agencyName':'Test agency','contactEmail':'agency@example.test',
     'reasons':['Culture fit is strong'],'tradeoffs':['Fixed schedule'], 'included':['Return flights','Four hotel nights'],
     'excluded':['Personal shopping'],'comparison':{'currency':'SGD','packageTotal':'1800','knownExtras':'0',
     'costComplete':True,'estimatedTotal':'1800','customVerified':True,'customTotal':'2500'}}
class Tests(unittest.TestCase):
    def render(self,result):
        pdf=PDF();scope['_agency_options'](pdf,result,lambda title:pdf.lines.append(title));return pdf
    def test_complete_options_both_modes_and_separate_totals(self):
        for mode in ('compare','packages_only'):
            pdf=self.render({'mode':mode,'status':'recommended','packages':[ROW]});text='\n'.join(pdf.lines)
            for expected in ('Agency package options','Tokyo Culture and Food','SGD 1,800.00','SGD 2,500.00','Return flights','Fixed schedule','agency@example.test','separate from the custom itinerary total'):
                self.assertIn(expected,text)
            self.assertEqual(pdf.pages,1)
    def test_unknowns_do_not_become_zero_or_within_budget(self):
        row={**ROW,'comparison':{'currency':'SGD','costComplete':False,'knownMinimumTotal':'1800','missingCosts':['meals']}}
        text='\n'.join(self.render({'packages':[row]}).lines)
        self.assertIn('Full trip total unknown',text);self.assertIn('Missing costs: meals',text)
        self.assertIn('Package price for your party: Unknown',text)
        self.assertNotIn('Estimated full trip total',text)
    def test_legacy_and_custom_only(self):
        for value in (None,{'mode':'custom_only','packages':[ROW]}):self.assertEqual(self.render(value).lines,[])
        self.assertIn('No agency package was recommended', '\n'.join(self.render({'packages':[]}).lines))
    def test_gateway_contract_and_agent_forwarding(self):
        schema=json.loads((ROOT/'lambdas/schemas/tp-render-pdf.json').read_text())
        self.assertIn('packageRecommendations',schema['inputSchema']['properties'])
        agent=ast.parse((ROOT/'agent/tp_travel_agent.py').read_text())
        calls=[n for n in ast.walk(agent) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='call' and n.args and isinstance(n.args[0],ast.Constant) and n.args[0].value=='render_pdf']
        self.assertEqual(len(calls),1)
        self.assertIn('packageRecommendations',ast.unparse(calls[0]))
        self.assertIn('package_result',ast.unparse(calls[0]))
if __name__=='__main__':unittest.main()
