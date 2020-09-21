#{{{ imports
import os
import bottle
import time
import sys
import datetime
import glob
import hashlib
import csv
import io
import string
import shlex
from urllib.parse import urlencode, quote as urlquote
from recoll import recoll, rclextract, rclconfig

def msg(s):
    print("%s" % s, file=sys.stderr)

# use ujson if avalible (faster than built in json)
try:
    import ujson as json
except ImportError:
    import json
    msg("ujson module not found, using (slower) built-in json module instead")


g_fscharset=sys.getfilesystemencoding()

#}}}
#{{{ settings
# settings defaults
DEFAULTS = {
    'context': 30,
    'stem': 1,
    'timefmt': '%c',
    'dirdepth': 2,
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

def get_topdirs(confdir):
    rclconf = rclconfig.RclConfig(confdir)
    return rclconf.getConfParam('topdirs')

# Environment fetch for the cases where we don't care if unset or null
def safe_envget(varnm):
    try:
        return os.environ[varnm]
    except Exception as ex:
        return None

# get database directory from recoll.conf, defaults to confdir/xapiandb. Note
# that this is available as getDbDir() in newer rclconfig versions
def get_dbdir(confdir):
    confdir = os.path.expanduser(confdir)
    rclconf = rclconfig.RclConfig(confdir)
    dbdir = rclconf.getConfParam('dbdir')
    if not dbdir:
        dbdir = 'xapiandb'
    if not os.path.isabs(dbdir):
        cachedir = rclconf.getConfParam('cachedir')
        if not cachedir:
            cachedir = confdir
        dbdir = os.path.join(cachedir, dbdir)
    # recoll API expects bytes, not strings
    return os.path.normpath(dbdir).encode(g_fscharset)
#}}}
#{{{ get_config
def get_config():
    config = {}
    envdir = safe_envget('RECOLL_CONFDIR')
    # get useful things from recoll.conf
    rclconf = rclconfig.RclConfig(envdir)
    config['confdir'] = rclconf.getConfDir()
    config['dirs'] = dict.fromkeys([os.path.expanduser(d) for d in
                      shlex.split(rclconf.getConfParam('topdirs'))],
                                   config['confdir'])
    # add topdirs from extra config dirs
    extraconfdirs = safe_envget('RECOLL_EXTRACONFDIRS')
    if extraconfdirs:
        config['extraconfdirs'] = shlex.split(extraconfdirs)
        for e in config['extraconfdirs']:
            config['dirs'].update(dict.fromkeys([os.path.expanduser(d) for d in
                shlex.split(get_topdirs(e))],e))
        config['extradbs'] = list(map(get_dbdir, config['extraconfdirs']))
    else:
        config['extraconfdirs'] = None
        config['extradbs'] = None
    config['stemlang'] = rclconf.getConfParam('indexstemminglanguages')
    # get config from cookies or defaults
    for k, v in DEFAULTS.items():
        value = select([bottle.request.get_cookie(k), v])
        config[k] = type(v)(value)
    # Fix csvfields: get rid of invalid ones to avoid needing tests in
    # the dump function
    cf = config['csvfields'].split()
    ncf = [f for f in cf if f in FIELDS]
    config['csvfields'] = ' '.join(ncf)
    config['fields'] = ' '.join(FIELDS)
    # get mountpoints
    config['mounts'] = {}
    for d in config['dirs']:
        name = 'mount_%s' % urlquote(d,'')
        config['mounts'][d] = select([bottle.request.get_cookie(name),
                                      'file://%s' % d], [None, ''])

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
        'query': select([bottle.request.query.query, '']),
        'before': select([bottle.request.query.before, '']),
        'after': select([bottle.request.query.after, '']),
        'dir': select([bottle.request.query.dir, '', '<all>'], [None, '']),
        'sort': select([bottle.request.query.sort, SORTS[0][0]]),
        'ascending': int(select([bottle.request.query.ascending, 0], [None, ''])),
        'page': int(select([bottle.request.query.page, 0], [None, ''])),
        'highlight': int(select([bottle.request.query.highlight, 1], [None, ''])),
        'snippets': int(select([bottle.request.query.snippets, 1], [None, ''])),
    }
    #msg("query['query'] : %s" % query['query'])
    return query
#}}}
#{{{ query_to_recoll_string
def query_to_recoll_string(q):
    qs = q['query']
    if len(q['after']) > 0 or len(q['before']) > 0:
        qs += " date:%s/%s" % (q['after'], q['before'])
    qdir = q['dir']
    if qdir != '<all>':
        qs += " dir:\"%s\" " % qdir
    return qs
#}}}
#{{{ recoll_initsearch
def recoll_initsearch(q):
    config = get_config()
    confdir = config['confdir']
    dbs = []
    """ The reason for this somewhat elaborate scheme is to keep the
    set size as small as possible by searching only those databases
    with matching topdirs """
    if q['dir'] == '<all>':
        if config['extraconfdirs']:
            dbs.extend(map(get_dbdir,config['extraconfdirs']))
    else:
        confdirs = []
        for d,conf in config['dirs'].items():
            tdbasename = os.path.basename(d)
            if os.path.commonprefix([tdbasename, q['dir']]) == tdbasename:
                confdirs.append(conf)
        if len(confdirs) == 0:
            # should not happen, using non-existing q['dir']?
            bottle.abort(400, 'no matching database for search directory ' + q['dir'])
        elif len(confdirs) == 1:
            # only one config (most common situation)
            confdir = confdirs[0]
        else:
            # more than one config with matching topdir, use 'm all
            confdir = confdirs[0]
            dbs.extend(map(get_dbdir, confdirs[1:]))

    if config['extradbs']:
        dbs.extend(config['extradbs'])

    if dbs:
        db = recoll.connect(confdir,dbs)
    else:
        db = recoll.connect(confdir)

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
def recoll_search(q):
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
            # Later Recoll versions return None at EOL instead of
            # exception This change restores conformance to PEP 249
            # Python Database API Specification
            if not doc:
                break
        except:
            break
        d = {}
        for f in FIELDS:
            v = getattr(doc, f)
            if v is not None:
                d[f] = v
            else:
                d[f] = ''
        d['label'] = select([d['title'], d['filename'], '?'], [None, ''])
        d['sha'] = hashlib.sha1((d['url']+d['ipath']).encode('utf-8')).hexdigest()
        d['time'] = timestr(d['mtime'], config['timefmt'])
        if 'snippets' in q and q['snippets']:
            if 'highlight' in q and q['highlight']:
                d['snippet'] = query.makedocabstract(
                    doc, highlighter)
            else:
                d['snippet'] = query.makedocabstract(doc)
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
             'config': config}
#}}}
#{{{ preview
@bottle.route('/preview/<resnum:int>')
def preview(resnum):
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
        'attachment; filename="%s"' % filename
    bottle.response.headers['Content-Length'] = os.stat(path).st_size
    f = open(path, 'rb')
    if pathismine:
        os.unlink(path)
    return f
#}}}
#{{{ json
@bottle.route('/json')
def get_json():
    query = get_query()
    qs = query_to_recoll_string(query)
    bottle.response.headers['Content-Type'] = 'application/json'
    bottle.response.headers['Content-Disposition'] = \
      'attachment; filename=recoll-%s.json' % normalise_filename(qs)
    res, nres, timer = recoll_search(query)
    ures = []
    for d in res:
        ud={}
        for f,v in d.items():
            ud[f] = v
        ures.append(ud)
    res = ures
    return json.dumps({ 'query': query, 'results': res })
#}}}
#{{{ csv
@bottle.route('/csv')
def get_csv():
    config = get_config()
    query = get_query()
    query['page'] = 0
    query['snippets'] = 0
    qs = query_to_recoll_string(query)
    bottle.response.headers['Content-Type'] = 'text/csv'
    bottle.response.headers['Content-Disposition'] = \
      'attachment; filename=recoll-%s.csv' % normalise_filename(qs)
    res, nres, timer = recoll_search(query)
    si = io.StringIO()
    cw = csv.writer(si)
    fields = config['csvfields'].split()
    cw.writerow(fields)
    for doc in res:
        row = []
        for f in fields:
            if f in doc:
                row.append(doc[f])
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
