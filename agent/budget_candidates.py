"""Optional safe budget-bound pruning; does not alter verification or quote values."""
import copy
from datetime import date
from decimal import Decimal,InvalidOperation

def prune(spec,pool):
 output=copy.deepcopy(pool);report={'enabled':True,'removed':{k:0 for k in ('flights','stays','transfers')},'status':'skipped'}
 if 'budget' not in spec or not spec.get('dailySpend'):return output,report
 if any(r.get('alreadyBooked') or r.get('mustUse') for r in pool.get('stays',[])):
  report['reason']='Explicit booking retained';return output,report
 currency=spec['budget']['currency']
 def price(row):
  value=row.get('price') or {}
  if value.get('currency')!=currency:raise ValueError('Unnormalized quote')
  amount=Decimal(str(value['amount']))
  if not amount.is_finite() or amount<0:raise ValueError('Invalid quote')
  return amount
 try:
  flights=pool['flights'];stays=pool['stays'];transfers=pool.get('transfers',[])
  if not flights or not stays:return output,report
  groups={('flight',0):flights}
  leg_count=len(spec.get('legs') or [spec])
  for leg in range(leg_count):
   rows=[r for r in stays if r.get('leg',0)==leg]
   if not rows:return output,report
   groups['stay',leg]=rows
  for hop in range(leg_count-1):
   rows=[r for r in transfers if r.get('hop',0)==hop]
   if not rows:return output,report
   groups['transfer',hop]=rows
  minimum={key:min(price(r) for r in rows) for key,rows in groups.items()}
  days=(date.fromisoformat(spec['endDate'])-date.fromisoformat(spec['startDate'])).days+1
  if spec['dailySpend']['currency']!=currency:return output,report
  daily=Decimal(str(spec['dailySpend']['amount']))*int(spec['pax'])*days
  limit=Decimal(str(spec['budget']['amount']));floor=sum(minimum.values(),daily)
  report.update(knownMinimumTotal=str(floor),currency=currency)
  if floor>limit:report.update(status='infeasible_quote_floor');return output,report
  keep={}
  for key,rows in groups.items():keep[key]=[r for r in rows if floor-minimum[key]+price(r)<=limit]
  output['flights']=keep['flight',0]
  output['stays']=[r for leg in range(leg_count) for r in keep['stay',leg]]
  output['transfers']=[r for hop in range(leg_count-1) for r in keep['transfer',hop]] if leg_count>1 else transfers
  report['removed']={key:len(pool.get(key,[]))-len(output.get(key,[])) for key in report['removed']}
  report['status']='applied'
  return output,report
 except (ValueError,KeyError,TypeError,InvalidOperation):
  report['reason']='Unknown or incompatible quote data; no candidates removed'
  return copy.deepcopy(pool),report
