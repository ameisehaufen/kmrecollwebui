from __future__ import print_function
#{{{ imports
import os
import bottle
import time
import sys
import datetime
import glob
import hashlib
import json
import csv
import io
import string
import shlex
def msg(s):
    print("%s" % s, file=sys.stderr)
py3k = sys.version_info >= (3, 0, 0)
if py3k:
    from urllib.parse import urlencode, quote as urlquote
else: # 2.x
    from urllib import urlencode, quote as urlquote

import urllib
# import recoll and rclextract
try:
    from recoll import recoll
    from recoll import rclextract
    hasrclextract = True
except Exception as err:
    msg("Import recoll because: %s" % err)
    import recoll
    hasrclextract = False
# Import rclconfig system-wide or local copy
try:
    from recoll import rclconfig
except:
    import rclconfig
#}}}
#{{{ settings
# settings defaults
DEFAULTS = {
    'context': 30,
    'stem': 1,
    'timefmt': '%c',
    'dirdepth': 3,
    'maxchars': 500,
    'maxresults': 0,
    'perpage': 25,
    'csvfields': 'filename title author size time mtype url',
    'title_link': 'download',
}

# sort fields/labels
SORTS = [
    ("relevancyrating", "Relevancy"),
    ("mtime", "Date",),
    ("url", "Path"),
    ("filename", "Filename"),
    ("fbytes", "Size"),
    ("author", "Author"),
]

# doc fields
FIELDS = [
    # exposed by python api
    'ipath',
    'filename',
    'title',
    'author',
    'fbytes',
    'dbytes',
    'size',
    'fmtime',
    'dmtime',
    'mtime',
    'mtype',
    'origcharset',
    'sig',
    'relevancyrating',
    'url',
    'abstract',
    'keywords',
    # calculated
    'time',
    'snippet',
    'label',
]
#}}}
#{{{  functions
#{{{  helpers
def select(ls, invalid=[None]):
    for value in ls:
        if value not in invalid:
            return value

def timestr(secs, fmt):
    if secs == '' or secs is None:
        secs = '0'
    t = time.gmtime(int(secs))
    return time.strftime(fmt, t)

def normalise_filename(fn):
    valid_chars = "_-%s%s" % (string.ascii_letters, string.digits)
    out = ""
    for i in range(0,len(fn)):
        if fn[i] in valid_chars:
            out += fn[i]
        else:
            out += "_"
    return out
#}}}
#{{{ get_config
def get_config():
    config = {}
    # get useful things from recoll.conf
    rclconf = rclconfig.RclConfig()
    config['confdir'] = rclconf.getConfDir()
    config['dirs'] = [os.path.expanduser(d) for d in
                      shlex.split(rclconf.getConfParam('topdirs'))]
    config['stemlang'] = rclconf.getConfParam('indexstemminglanguages')
    # get config from cookies or defaults
    for k, v in DEFAULTS.items():
        value = select([bottle.request.get_cookie(k), v])
        config[k] = type(v)(value)
    # Fix csvfields: get rid of invalid ones to avoid needing tests in the dump function
    cf = config['csvfields'].split()
    ncf = [f for f in cf if f in FIELDS]
    config['csvfields'] = ' '.join(ncf)
    config['fields'] = ' '.join(FIELDS)
    # get mountpoints
    config['mounts'] = {}
    for d in config['dirs']:
        name = 'mount_%s' % urlquote(d,'')
        config['mounts'][d] = select([bottle.request.get_cookie(name), 'file://%s' % d], [None, ''])

    # Parameters set by the admin in the recoll configuration
    # file. These override anything else, so read them last
    val = rclconf.getConfParam('webui_nojsoncsv')
    val = 0 if val is None else int(val)
    config['rclc_nojsoncsv'] = val

    val = rclconf.getConfParam('webui_maxperpage')
    val = 0 if val is None else int(val)
    if val:
        if config['perpage'] == 0 or config['perpage'] > val:
            config['perpage'] = val

    val = rclconf.getConfParam('webui_nosettings')
    val = 0 if val is None else int(val)
    config['rclc_nosettings'] = val

    return config
#}}}
#{{{ get_dirs
def get_dirs(tops, depth):
    v = []
    for top in tops:
        dirs = [top]
        for d in range(1, depth+1):
            dirs = dirs + glob.glob(top + '/*' * d)
        dirs = filter(lambda f: os.path.isdir(f), dirs)
        top_path = top.rsplit('/', 1)[0]
        dirs = [w.replace(top_path+'/', '', 1) for w in dirs]
        v = v + dirs
    return ['<all>'] + v
#}}}
#{{{ get_query
def get_query():
    query = {
        'query': select([bottle.request.query.get('query'), '']),
        'before': select([bottle.request.query.get('before'), '']),
        'after': select([bottle.request.query.get('after'), '']),
        'dir': select([bottle.request.query.get('dir'), '', '<all>'], [None, '']),
        'sort': select([bottle.request.query.get('sort'), SORTS[0][0]]),
        'ascending': int(select([bottle.request.query.get('ascending'), 0])),
        'page': int(select([bottle.request.query.get('page'), 0])),
    }
    return query
#}}}
#{{{ query_to_recoll_string
def query_to_recoll_string(q):
    if type(q['query']) == type(u''):
        qs = q['query']
    else:
        qs = q['query'].decode('utf-8')
    if len(q['after']) > 0 or len(q['before']) > 0:
        qs += " date:%s/%s" % (q['after'], q['before'])
    qdir = q['dir']
    if type(qdir) != type(u''):
        qdir = qdir.decode('utf-8')
    if qdir != '<all>':
        qs += " dir:\"%s\" " % qdir
    return qs
#}}}
#{{{ recoll_initsearch
def recoll_initsearch(q):
    config = get_config()
    db = recoll.connect(config['confdir'])
    db.setAbstractParams(config['maxchars'], config['context'])
    query = db.query()
    query.sortby(q['sort'], q['ascending'])
    try:
        qs = query_to_recoll_string(q)
        query.execute(qs, config['stem'], config['stemlang'])
    except:
        pass
    return query
#}}}
#{{{ HlMeths
class HlMeths:
    def startMatch(self, idx):
        return '<span class="search-result-highlight">'
    def endMatch(self):
        return '</span>'
#}}}
#{{{ recoll_search
def recoll_search(q, dosnippets=True):
    config = get_config()
    tstart = datetime.datetime.now()
    results = []
    query = recoll_initsearch(q)
    nres = query.rowcount

    if config['maxresults'] == 0:
        config['maxresults'] = nres
    if nres > config['maxresults']:
        nres = config['maxresults']
    if config['perpage'] == 0 or q['page'] == 0:
        config['perpage'] = nres
        q['page'] = 1
    offset = (q['page'] - 1) * config['perpage']

    if query.rowcount > 0:
        if type(query.next) == int:
            query.next = offset
        else:
            query.scroll(offset, mode='absolute')

    highlighter = HlMeths()
    for i in range(config['perpage']):
        try:
            doc = query.fetchone()
        except:
            break
        d = {}
        for f in FIELDS:
            v = getattr(doc, f)
            if v is not None:
                d[f] = v.encode('utf-8')
            else:
                d[f] = b''
        d['label'] = select([d['title'], d['filename'], '?'], [None, ''])
        d['sha'] = hashlib.sha1(d['url']+d['ipath']).hexdigest().encode('utf-8')
        d['time'] = timestr(d['mtime'], config['timefmt']).encode('utf-8')
        if dosnippets:
            d['snippet'] = query.makedocabstract(doc, highlighter).encode('utf-8')
        #for n,v in d.items():
        #    print("type(%s) is %s" % (n,type(v)))
        results.append(d)
    tend = datetime.datetime.now()
    return results, nres, tend - tstart
#}}}
#}}}
#{{{ routes
#{{{ static
@bottle.route('/static/:path#.+#')
def server_static(path):
    return bottle.static_file(path, root='./static')
#}}}
#{{{ main
@bottle.route('/')
@bottle.view('main')
def main():
    config = get_config()
    return { 'dirs': get_dirs(config['dirs'], config['dirdepth']),
            'query': get_query(), 'sorts': SORTS, 'config': config}
#}}}
#{{{ results
@bottle.route('/results')
@bottle.view('results')
def results():
    config = get_config()
    query = get_query()
    qs = query_to_recoll_string(query)
    res, nres, timer = recoll_search(query)
    if config['maxresults'] == 0:
        config['maxresults'] = nres
    if config['perpage'] == 0:
        config['perpage'] = nres
    return { 'res': res, 'time': timer, 'query': query, 'dirs':
            get_dirs(config['dirs'], config['dirdepth']),
             'qs': qs, 'sorts': SORTS, 'config': config,
            'query_string': bottle.request.query_string, 'nres': nres,
             'hasrclextract': hasrclextract, 'config': config}
#}}}
#{{{ preview
@bottle.route('/preview/<resnum:int>')
def preview(resnum):
    if not hasrclextract:
        return 'Sorry, needs recoll version 1.19 or later'
    query = get_query()
    qs = query_to_recoll_string(query)
    rclq = recoll_initsearch(query)
    if resnum > rclq.rowcount - 1:
        return 'Bad result index %d' % resnum
    rclq.scroll(resnum)
    doc = rclq.fetchone()
    xt = rclextract.Extractor(doc)
    tdoc = xt.textextract(doc.ipath)
    if tdoc.mimetype == 'text/html':
        bottle.response.content_type = 'text/html; charset=utf-8'
    else:
        bottle.response.content_type = 'text/plain; charset=utf-8'
    return tdoc.text
#}}}
#{{{ download
@bottle.route('/download/<resnum:int>')
def edit(resnum):
    if not hasrclextract:
        return 'Sorry, needs recoll version 1.19 or later'
    query = get_query()
    qs = query_to_recoll_string(query)
    rclq = recoll_initsearch(query)
    if resnum > rclq.rowcount - 1:
        return 'Bad result index %d' % resnum
    rclq.scroll(resnum)
    doc = rclq.fetchone()
    bottle.response.content_type = doc.mimetype
    pathismine = False

    xt = rclextract.Extractor(doc)
    path = xt.idoctofile(doc.ipath, doc.mimetype)
    pathismine = True

    if (not doc.ipath) and "filename" in doc.keys():
        filename = doc.filename
    else:
        filename = os.path.basename(path)
    bottle.response.headers['Content-Disposition'] = \
        'attachment; filename="%s"' % filename.encode('utf-8')
    path = path.encode('utf-8')
    bottle.response.headers['Content-Length'] = os.stat(path).st_size
    f = open(path, 'r')
    if pathismine:
        os.unlink(path)
    return f
#}}}
#{{{ json
@bottle.route('/json')
def get_json():
    query = get_query()
    query['page'] = 0
    qs = query_to_recoll_string(query)
    bottle.response.headers['Content-Type'] = 'application/json'
    bottle.response.headers['Content-Disposition'] = 'attachment; filename=recoll-%s.json' % normalise_filename(qs)
    res, nres, timer = recoll_search(query)

    return json.dumps({ 'query': query, 'results': res })
#}}}
#{{{ csv
@bottle.route('/csv')
def get_csv():
    config = get_config()
    query = get_query()
    query['page'] = 0
    qs = query_to_recoll_string(query)
    bottle.response.headers['Content-Type'] = 'text/csv'
    bottle.response.headers['Content-Disposition'] = 'attachment; filename=recoll-%s.csv' % normalise_filename(qs)
    res, nres, timer = recoll_search(query, False)
    if py3k:
        si = io.StringIO()
    else:
        si = io.BytesIO()
    cw = csv.writer(si)
    fields = config['csvfields'].split()
    cw.writerow(fields)
    for doc in res:
        row = []
        for f in fields:
            if f in doc:
                row.append(doc[f].decode('utf-8'))
            else:
                row.append('')
        cw.writerow(row)
    return si.getvalue().strip("\r\n")
#}}}
#{{{ settings/set
@bottle.route('/settings')
@bottle.view('settings')
def settings():
    return get_config()

@bottle.route('/set')
def set():
    config = get_config()
    for k, v in DEFAULTS.items():
        bottle.response.set_cookie(k, str(bottle.request.query.get(k)), max_age=3153600000, expires=315360000)
    for d in config['dirs']:
        cookie_name = 'mount_%s' % urlquote(d, '')
        bottle.response.set_cookie(cookie_name, str(bottle.request.query.get('mount_%s' % d)), max_age=3153600000, expires=315360000)
    bottle.redirect('./')
#}}}
#{{{ osd
@bottle.route('/osd.xml')
@bottle.view('osd')
def main():
    #config = get_config()
    url = bottle.request.urlparts
    url = '%s://%s' % (url.scheme, url.netloc)
    return {'url': url}
#}}}
# vim: fdm=marker:tw=80:ts=4:sw=4:sts=4:et

