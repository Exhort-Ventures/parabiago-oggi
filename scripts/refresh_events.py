#!/usr/bin/env python3
"""Refresh the two public Parabiago Oggi area feeds.

Adapters deliberately consume portable, inspectable formats first (JSON-LD, RSS
and WordPress JSON); semantic HTML is a fallback.  A bad source cannot prevent
the other area from being published.
"""
from __future__ import annotations
import argparse, hashlib, json, math, re, unicodedata
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]; TZ=ZoneInfo("Europe/Rome")
CONFIG=ROOT/'config/areas.json'; OUT=ROOT/'data/areas'; HEALTH=ROOT/'data/source-health.json'
REPORT_JSON=ROOT/'data/coverage-report.json'; REPORT_MD=ROOT/'data/coverage-report.md'; FRESHNESS=ROOT/'data/source-freshness.json'
CENSUS_JSON=ROOT/'data/source-census.json'; CENSUS_MD=ROOT/'data/source-census.md'
S=requests.Session(); S.headers.update({'User-Agent':'ParabiagoOggi/3.0 (+https://github.com/Exhort-Ventures/parabiago-oggi)','Accept-Language':'it-IT,it;q=0.9'})
MONTHS={'gennaio':1,'febbraio':2,'marzo':3,'aprile':4,'maggio':5,'giugno':6,'luglio':7,'agosto':8,'settembre':9,'ottobre':10,'novembre':11,'dicembre':12,'gen':1,'feb':2,'mar':3,'apr':4,'mag':5,'giu':6,'lug':7,'ago':8,'set':9,'ott':10,'nov':11,'dic':12}
CATS={'nightlife':['dj','discoteca','club','aperitivo','serata','dance'],'music':['concerto','musica','jazz','live','guitar'],'festivals':['festival','rassegna'],'food':['sagra','festa','mercato','degustazione','patata','uva','fungo','food'],'cinema':['cinema','film','proiezione'],'sport':['gara','corsa','trail','bike','cicl','sci','rally','canoa','torneo'],'outdoor':['escursione','camminata','trekking','montagna'],'workshops':['laboratorio','workshop','corso'],'community':['fiera','comunit','patronale'],'culture':['mostra','teatro','libro','museo','visita','cultura']}
LABELS={'nightlife':'Vita notturna','music':'Musica','festivals':'Festival','food':'Food e sagre','cinema':'Cinema','sport':'Sport e gare','outdoor':'Montagna e outdoor','workshops':'Workshop','community':'Comunità','culture':'Cultura','other':'Altro'}

def clean(v): return re.sub(r'\s+',' ',str(v or '')).strip()
def norm(v): return re.sub(r'[^a-z0-9]+',' ',unicodedata.normalize('NFKD',clean(v).lower()).encode('ascii','ignore').decode()).strip()
def dist(a,b,c,d):
 p1,p2=math.radians(a),math.radians(c); dp,dl=math.radians(c-a),math.radians(d-b)
 return 6371*2*math.atan2(math.sqrt(math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2),math.sqrt(1-(math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2)))
def date_it(text, now):
 text=clean(text).lower().replace('1°','1'); m=re.search(r'(\d{1,2})\s*(?:(?:[-–]|al)\s*(\d{1,2})\s*)?([a-zà]+)\.?\s*(\d{4})?',text)
 if not m or m.group(3) not in MONTHS:return None,None
 y=int(m.group(4) or now.year); mo=MONTHS[m.group(3)]; d=int(m.group(1)); end=int(m.group(2) or d)
 try:
  start=datetime(y,mo,d,tzinfo=TZ); finish=datetime(y,mo,end,23,59,tzinfo=TZ)
  if not m.group(4) and finish < now-timedelta(days=30): start=start.replace(year=y+1);finish=finish.replace(year=y+1)
  clock=re.search(r'(?:ore|alle|dalle)?\s*(\d{1,2})[.:](\d{2})',text)
  if clock:start=start.replace(hour=int(clock.group(1)),minute=int(clock.group(2)))
  return start,finish if end!=d else None
 except ValueError:return None,None
def iso_dt(v,now):
 if not v:return None
 try:return datetime.fromisoformat(v.replace('Z','+00:00')).astimezone(TZ)
 except ValueError:return date_it(v,now)[0]
def category(text):
 scores={k:sum(x in norm(text) for x in v) for k,v in CATS.items()}; k=max(scores,key=scores.get);return k if scores[k] else 'other'
def coords(place,area):
 if 'busto arsizio' in norm(place): return 45.6101,8.8496,'Busto Arsizio'
 key=norm(place)
 for name,p in area.get('aliases',{}).items():
  if norm(name) in key:return p['lat'],p['lng'],name
 return area['latitude'],area['longitude'],area['centreName']
def confidence(source): return source.get('confidence','Da verificare')
def event(raw,source,area,now):
 title=clean(raw.get('name') or raw.get('title')); start=iso_dt(raw.get('startDate') or raw.get('start'),now)
 if not title or not start:return None
 place=raw.get('location') or raw.get('city') or source.get('city') or area['centreName']
 if isinstance(place,dict): address=place.get('address',{}); address=address if isinstance(address,dict) else {}; city=address.get('addressLocality') or place.get('name') or raw.get('city') or source.get('city') or area['centreName']; venue=place.get('name') or city; addr=address.get('streetAddress','')
 else: city=venue=clean(place);addr=''
 lat,lng,located=coords(f'{venue} {city} {addr}',area); d=dist(area['latitude'],area['longitude'],lat,lng); cat=category(f'{title} {raw.get("description","")}')
 return {'title':title,'start':start.isoformat(),'end':(iso_dt(raw.get('endDate') or raw.get('end'),now) or raw.get('_end')).isoformat() if (raw.get('endDate') or raw.get('end') or raw.get('_end')) else None,'venue':clean(venue),'address':clean(addr),'city':clean(city),'latitude':lat,'longitude':lng,'distanceKm':round(d,1),'category':cat,'categoryLabel':LABELS[cat],'description':clean(raw.get('description'))[:900],'free':raw.get('isAccessibleForFree') if isinstance(raw.get('isAccessibleForFree'),bool) else None,'priceText':clean(raw.get('offers',{}).get('price') if isinstance(raw.get('offers'),dict) else raw.get('priceText')),'bookingUrl':raw.get('url') or raw.get('bookingUrl'),'sourceName':source['name'],'sourceUrl':raw.get('url') or source['url'],'sourceType':source['type'],'confidence':confidence(source),'organiser':clean((raw.get('organizer') or {}).get('name') if isinstance(raw.get('organizer'),dict) else raw.get('organiser')),'imageUrl':raw.get('image') if isinstance(raw.get('image'),str) else None,'lastCheckedAt':now.isoformat(),'areaId':area['id'],'locationPrecision':'town'}
def jsonld(source,now):
 soup=BeautifulSoup(S.get(source['url'],timeout=30).text,'html.parser'); found=[]
 for s in soup.select('script[type="application/ld+json"]'):
  try: payload=json.loads(s.string or '{}')
  except json.JSONDecodeError: continue
  stack=payload if isinstance(payload,list) else payload.get('@graph',[payload])
  for x in stack:
   if isinstance(x,dict) and ('Event' in str(x.get('@type',''))):found.append(x)
 return found
def html_cards(source,now):
 soup=BeautifulSoup(S.get(source['url'],timeout=30).text,'html.parser'); out=[]
 for node in soup.select('article, .event, .evento, .views-row'):
  text=clean(node.get_text(' ',strip=True)); start,end=date_it(text,now); a=node.select_one('a[href]'); h=node.select_one('h1,h2,h3,h4')
  if start and a and h:out.append({'name':clean(h.text),'start':start.isoformat(),'end':end.isoformat() if end else None,'location':source.get('city',source['name']),'description':text,'url':requests.compat.urljoin(source['url'],a['href'])})
 return out
def cheventi(source,now):
 soup=BeautifulSoup(S.get(source['url'],timeout=30).text,'html.parser'); out=[]
 for node in soup.find_all('li'):
  text=clean(node.get_text(' ',strip=True)); start,end=date_it(text,now)
  if not start or len(text)<30: continue
  a=node.select_one('a[href]'); city=re.search(r"\ba\s+([A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÿ’' -]{2,40})\s+(?:da|sabato|domenica|lunedì|martedì|mercoledì|giovedì|venerdì)",text)
  title=re.sub(r'^.*?[–-]\s*','',text).split(' da ')[0].split(' sabato ')[0].split(' domenica ')[0]
  out.append({'name':title,'start':start.isoformat(),'end':end.isoformat() if end else None,'location':city.group(1) if city else source.get('city','Milano'),'description':text,'url':requests.compat.urljoin(source['url'],a['href']) if a else source['url']})
 return out
def visitossola(source,now):
 page=S.get(source['url'],timeout=30).text; nonce=re.search(r'ajax_nonce"\s*:\s*"([^"]+)',page).group(1)
 p={'action':'get_news_and_events_request','ajax_nonce':nonce,'post_id':'140','post_type':'event','from_date_filter':now.strftime('%Y/%m/%d'),'to_date_filter':(now+timedelta(days=95)).strftime('%Y/%m/%d'),'geo_filter_id':'','interest_filter_id':'','page':1,'how_many':100}
 items=S.get('https://www.visitossola.it/wp-admin/admin-ajax.php',params=p,timeout=30).json().get('items',[]);out=[]
 for item in items:
  detail=jsonld({'url':item['link']},now)
  if detail: out.extend(detail);continue
  start,end=date_it(clean(item['title']),now)
  if start:out.append({'name':clean(item['title']),'start':start.isoformat(),'end':end.isoformat() if end else None,'location':item.get('geo',[{}])[0].get('title','Domodossola'),'description':item.get('abstract',''),'url':item['link'],'image':item.get('background')})
 return out
def curated(source,now):
 return source.get('records',[])
def article_dates(source,now):
 """Extract dated programme entries from a public article/PDF landing page."""
 soup=BeautifulSoup(S.get(source['url'],timeout=30).text,'html.parser'); text=clean(soup.get_text(' ',strip=True)); title=clean((soup.select_one('h1') or soup.title).get_text(' ',strip=True))
 out=[]
 for match in re.finditer(r'(?:dal\s+)?\d{1,2}\s*(?:[-–]|al)?\s*\d{0,2}\s*(?:gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)\s+2026',text,re.I):
  start,end=date_it(match.group(0),now)
  if start: out.append({'name':title,'start':start.isoformat(),'end':end.isoformat() if end else None,'location':source.get('city',source['name']),'description':text[:850],'url':source['url']})
 return out
def ba_estate_pdf(source,now):
 """Parse Busto's official monthly programme; date headings precede activity names."""
 import io
 text='\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(S.get(source['url'],timeout=60).content)).pages); lines=[clean(x) for x in text.splitlines() if clean(x)]; out=[]
 for i,line in enumerate(lines):
  m=re.match(r'(Sabato|Domenica|Venerdì|Mercoledì|Giovedì|Martedì|Lunedì)\s+(\d{1,2})(?:\s*[-–]\s*(\d{1,2}:\d{2}))?',line,re.I)
  if not m: continue
  month=9 if i>len(lines)//2 else 8; hour=m.group(3) or '18:00'; title=next((x for x in lines[i+1:i+6] if len(x)>5 and not re.match(r'(BA |Parco|Piazza|Via |ASSOCIAZIONE|Regia)',x,re.I)),None)
  if title: out.append({'name':title.title(),'start':f'2026-{month:02d}-{int(m.group(2)):02d}T{hour}:00+02:00','location':'Busto Arsizio','description':'Programma ufficiale BA Estate 2026','url':source['landingUrl']})
 return out
def ical(source,now):
 """Minimal dependency-free iCalendar adapter for public municipal calendars."""
 text=S.get(source['url'],timeout=30).text.replace('\r\n ','').replace('\r\n\t',''); out=[]
 for block in text.split('BEGIN:VEVENT')[1:]:
  fields=dict(re.findall(r'^(SUMMARY|DTSTART[^:]*|DTEND[^:]*|LOCATION|URL|DESCRIPTION):(.+)$',block,re.M))
  start=fields.get(next((k for k in fields if k.startswith('DTSTART')),''),'')
  if re.fullmatch(r'\d{8}',start): start=f'{start[:4]}-{start[4:6]}-{start[6:]}T12:00:00+02:00'
  elif re.fullmatch(r'\d{8}T\d{6}Z?',start): start=f'{start[:4]}-{start[4:6]}-{start[6:8]}T{start[9:11]}:{start[11:13]}:{start[13:15]}+02:00'
  if fields.get('SUMMARY') and start: out.append({'name':clean(fields['SUMMARY']),'start':start,'location':clean(fields.get('LOCATION',source.get('city',''))),'description':clean(fields.get('DESCRIPTION','')),'url':fields.get('URL',source['url'])})
 return out
def legacy(source,now):
 try:
  old=json.loads((ROOT/'data/events.json').read_text()).get('events',[])
  return [{'name':x['title'],'start':x['start'],'end':x.get('end'),'location':x.get('city'),'description':x.get('description',''),'url':x.get('sourceUrl'),'isAccessibleForFree':x.get('free'),'priceText':x.get('price')} for x in old]
 except FileNotFoundError:return []
def collect_source(source,now):
 if source['adapter']=='jsonld':return jsonld(source,now)
 if source['adapter']=='cheventi':return cheventi(source,now)
 if source['adapter']=='visitossola':return visitossola(source,now)
 if source['adapter']=='curated':return curated(source,now)
 if source['adapter']=='legacy':return legacy(source,now)
 if source['adapter']=='article_dates':return article_dates(source,now)
 if source['adapter']=='ical':return ical(source,now)
 if source['adapter']=='ba_estate_pdf':return ba_estate_pdf(source,now)
 return html_cards(source,now)
def duplicate(a,b): return abs((iso_dt(a['start'],datetime.now(TZ))-iso_dt(b['start'],datetime.now(TZ))).total_seconds())<4*3600 and SequenceMatcher(None,norm(a['title']),norm(b['title'])).ratio()>.78 and dist(a['latitude'],a['longitude'],b['latitude'],b['longitude'])<2
def rank(e,now):
 score={'Confermato':30,'Probabile':18,'Da verificare':8}[e['confidence']]+max(0,20-e['distanceKm'])
 if e['category'] in ('food','festivals','music','sport','nightlife','outdoor'):score+=12
 if iso_dt(e['start'],now).weekday()>=4:score+=8
 if iso_dt(e['start'],now).hour>=18:score+=5
 return score
def series_id(e):
 """Return a stable programme id only for genuinely repeated programme sessions."""
 title=norm(e['title'])
 if 'agosto in piazza' in title and 'aperitivo' not in title:
  return f"{e['areaId']}:agosto-in-piazza-2026"
 return None
def coverage(data, previous, now):
 events=data['events']; series={e.get('recurringSeriesId') or e['id'] for e in events}; by_source={s['name']:s['acceptedRecords'] for s in data['sourceHealth']}; counts={}
 for e in events: counts[e.get('recurringSeriesId') or e['id']]=counts.get(e.get('recurringSeriesId') or e['id'],0)+1
 largest=max(counts.values(),default=0); warnings=[]; prior=(previous or {}).get('totalFutureDateRecords',len(events))
 if prior and len(events)<prior*.7:warnings.append('WARNING: event count fell by more than 30%')
 if not events:warnings.append('WARNING: zero events')
 if largest and largest/len(events)>.4:warnings.append('WARNING: one programme exceeds 40% of dates')
 if not any(iso_dt(e['start'],now)<=now+timedelta(days=7) for e in events):warnings.append('WARNING: no events in next 7 days')
 return {'totalFutureDateRecords':len(events),'distinctEventSeries':len(series),'representedTowns':sorted({e['city'] for e in events}),'representedCategories':sorted({e['category'] for e in events}),'representedSourceFamilies':sorted({e['sourceType'] for e in events}),'acceptedRecordsBySource':by_source,'zeroResultSources':[s['name'] for s in data['sourceHealth'] if not s['acceptedRecords'] and s['fetchStatus']=='ok'],'failedSources':[s['name'] for s in data['sourceHealth'] if s['fetchStatus']=='failed'],'oldestEventDate':min((e['start'] for e in events),default=None),'newestEventDate':max((e['start'] for e in events),default=None),'largestProgrammeShare':round(largest/len(events),3) if events else 0,'eventsExpiringNext7Days':sum(bool(e.get('end')) and iso_dt(e['end'],now)<=now+timedelta(days=7) for e in events),'comparisonWithPreviousSuccessfulRefresh':{'previousCount':prior,'change':len(events)-prior},'warnings':warnings}
def refresh(area,now):
 raw=[]; health=[]; rejected={'date':0,'location':0,'radius':0,'duplicate':0}; trace=[]
 for source in area['sources']:
  report={'id':source['id'],'name':source['name'],'fetchStatus':'ok','rawRecordsFound':0,'recordsParsed':0,'recordsRejectedByDate':0,'recordsRejectedByLocation':0,'recordsRejectedByRadius':0,'recordsRejectedAsDuplicates':0,'acceptedRecords':0,'failureMessage':None}
  try: records=collect_source(source,now); report['rawRecordsFound']=report['recordsParsed']=len(records)
  except Exception as ex: records=[];report['fetchStatus']='failed';report['failureMessage']=str(ex)[:220]
  for r in records:
   e=event(r,source,area,now)
   row={'source':source['name'],'title':clean(r.get('name') or r.get('title')),'parsedDate':r.get('start') or r.get('startDate'),'parsedTown':r.get('location') or r.get('city'),'area':area['id'],'terminalState':None,'finalEventId':None,'outputFile':str(OUT/f"{area['id']}.json")}
   if not e:rejected['date']+=1;report['recordsRejectedByDate']+=1;row['terminalState']='INVALID_DATE';trace.append(row);continue
   row.update({'coordinates':[e['latitude'],e['longitude']],'distanceKm':e['distanceKm']})
   end=iso_dt(e['end'],now) if e['end'] else iso_dt(e['start'],now)
   if end < now or iso_dt(e['start'],now)>now+timedelta(days=area['horizonDays']):rejected['date']+=1;report['recordsRejectedByDate']+=1;row['terminalState']='OUTSIDE_DATE_WINDOW';trace.append(row);continue
   if e['distanceKm']>area['radiusKm']:rejected['radius']+=1;report['recordsRejectedByRadius']+=1;row['terminalState']='OUTSIDE_RADIUS';trace.append(row);continue
   match=next((x for x in raw if duplicate(e,x)),None)
   if match:
    rejected['duplicate']+=1;report['recordsRejectedAsDuplicates']+=1
    row['terminalState']='DUPLICATE_OF_EXISTING'; row['canonicalTitle']=match['title']; row['canonicalStart']=match['start'];trace.append(row)
    if e['confidence']=='Confermato':match.update(e)
    elif match['confidence']!= 'Confermato':match['confidence']='Confermato' # independent agreement
    continue
   raw.append(e);report['acceptedRecords']+=1
   row['terminalState']='INCLUDED_IN_FINAL_DATASET';trace.append(row)
  health.append(report)
 for e in raw:
  e['rankingScore']=rank(e,now); e['recurringSeriesId']=series_id(e)
  e['id']=hashlib.sha1(f"{e['areaId']}|{norm(e['title'])}|{e['start']}|{e['city']}".encode()).hexdigest()[:16]
 for row in trace:
  if row['terminalState']=='INCLUDED_IN_FINAL_DATASET':
   found=next((e for e in raw if e['sourceName']==row['source'] and e['title']==row['title']),None)
   if found: row['finalEventId']=found['id']
  if row['terminalState']=='DUPLICATE_OF_EXISTING':
   found=next((e for e in raw if e['title']==row.get('canonicalTitle') and e['start']==row.get('canonicalStart')),None)
   if found: row['canonicalDuplicateId']=found['id']
 for report in health: report['acceptedRecords']=sum(e['sourceName']==report['name'] for e in raw)
 raw.sort(key=lambda x:(-x['rankingScore'],x['start']))
 return {'area':{k:area[k] for k in ('id','displayName','centreName','latitude','longitude','radiusKm','horizonDays','defaultLanguage')},'updatedAt':now.isoformat(),'eventCount':len(raw),'events':raw,'sourceHealth':health,'rejections':rejected,'pipelineTrace':trace}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--area');ap.add_argument('--offline',action='store_true');args=ap.parse_args(); now=datetime.now(TZ)
 areas=json.loads(CONFIG.read_text())['areas']; next(a for a in areas if a['id']=='parabiago')['horizonDays']=90; OUT.mkdir(parents=True,exist_ok=True); index=[];all_health={}; old=json.loads(REPORT_JSON.read_text()) if REPORT_JSON.exists() else {}; report={'generatedAt':now.isoformat(),'areas':{}}
 for area in areas:
  if args.area and area['id']!=args.area:continue
  data=refresh(area,now); (OUT/f"{area['id']}.json").write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');index.append(data['area']|{'eventCount':data['eventCount'],'updatedAt':data['updatedAt']});all_health[area['id']]={'sources':data['sourceHealth'],'rejections':data['rejections']};report['areas'][area['id']]=coverage(data,old.get('areas',{}).get(area['id']),now);print(f"{area['id']}: {data['eventCount']} accepted")
 (ROOT/'data/areas.json').write_text(json.dumps({'updatedAt':now.isoformat(),'areas':index},ensure_ascii=False,indent=2)+'\n');HEALTH.write_text(json.dumps({'updatedAt':now.isoformat(),'areas':all_health},ensure_ascii=False,indent=2)+'\n')
 REPORT_JSON.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n'); REPORT_MD.write_text('# Coverage report\n\n'+''.join(f"## {a}\n\n- Records: {v['totalFutureDateRecords']}\n- Series: {v['distinctEventSeries']}\n- Towns: {', '.join(v['representedTowns']) or 'none'}\n- Warnings: {', '.join(v['warnings']) or 'none'}\n\n" for a,v in report['areas'].items()))
 census={'generatedAt':now.isoformat(),'sources':[]}
 for area in areas:
  for source in area['sources']:
   health=next((s for s in all_health.get(area['id'],{}).get('sources',[]) if s['id']==source['id']),{})
   census['sources'].append({'area':area['id'],'name':source['name'],'url':source['url'],'extractionMethod':source['adapter'],'paginationChecked':False,'rawRecordsFound':health.get('rawRecordsFound',0),'futureDatedRecordsFound':health.get('recordsParsed',0),'acceptedRecords':health.get('acceptedRecords',0),'duplicatesRemoved':health.get('recordsRejectedAsDuplicates',0),'zeroResultReason':health.get('failureMessage') or ('no usable future dated records' if not health.get('acceptedRecords') else None),'lastCheckedAt':now.isoformat()})
 CENSUS_JSON.write_text(json.dumps(census,ensure_ascii=False,indent=2)+'\n'); CENSUS_MD.write_text('# Source census\n\n'+''.join(f"- **{x['area']} · {x['name']}** — {x['extractionMethod']}; raw {x['rawRecordsFound']}; accepted {x['acceptedRecords']}; {x['zeroResultReason'] or 'active'}\n" for x in census['sources']))
if __name__=='__main__':main()
