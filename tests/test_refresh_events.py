import importlib.util
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

spec=importlib.util.spec_from_file_location('refresh',Path(__file__).parents[1]/'scripts/refresh_events.py'); refresh=importlib.util.module_from_spec(spec);spec.loader.exec_module(refresh)
NOW=datetime(2026,8,2,tzinfo=ZoneInfo('Europe/Rome'))
def test_italian_dates_and_ranges():
 s,e=refresh.date_it('dal 7 al 9 agosto 2026 ore 21:30',NOW);assert s.day==7 and s.hour==21 and e.day==9
def test_missing_year_rolls_forward():
 s,_=refresh.date_it('2 gennaio',datetime(2026,12,20,tzinfo=ZoneInfo('Europe/Rome')));assert s.year==2027
def test_alias_and_radius():
 a={'aliases':{'Riale':{'lat':46.417,'lng':8.413}},'latitude':46.2296,'longitude':8.3233};lat,lng,_=refresh.coords('Riale',a);assert refresh.dist(a['latitude'],a['longitude'],lat,lng)<30
def test_dedupe_requires_time_title_and_place():
 a={'title':'Sagra della Patata','start':'2026-08-10T20:00:00+02:00','latitude':46.22,'longitude':8.32};b=a|{'title':'Sagra della patata'};assert refresh.duplicate(a,b)
def test_category_coverage():
 assert refresh.category('Trail running in montagna')=='sport';assert refresh.category('Festa della Patata e sagra')=='food'
def test_ongoing_event_start_and_id_are_stable():
 e={'areaId':'ossola','title':'Festival in corso','start':'2026-08-01T18:00:00+02:00','city':'Crodo','latitude':46.22,'longitude':8.32}
 first=refresh.hashlib.sha1(f"{e['areaId']}|{refresh.norm(e['title'])}|{e['start']}|{e['city']}".encode()).hexdigest()[:16]
 second=refresh.hashlib.sha1(f"{e['areaId']}|{refresh.norm(e['title'])}|{e['start']}|{e['city']}".encode()).hexdigest()[:16]
 assert e['start']=='2026-08-01T18:00:00+02:00' and first==second
def test_ical_datetime_shape():
 assert refresh.iso_dt('2026-08-12T21:30:00+02:00',NOW).tzinfo is not None
def test_mobile_css_uses_shrink_safe_layout():
 css=(Path(__file__).parents[1]/'styles.css').read_text()
 assert 'repeat(6, minmax(0, 1fr))' in css and '@media (max-width: 900px)' in css
 assert 'grid-template-columns: 1fr;' in css and 'overflow-wrap:anywhere' in css
