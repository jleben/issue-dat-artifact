#!/usr/bin/env python

import sys
import optparse
import pprint
import json
import re
import requests
from bs4 import BeautifulSoup
from getpass import getpass


# options

parser = optparse.OptionParser(usage='Usage: %prog [options] sfexport.xml tracker githubuser/repo')

parser.add_option('-u', '--user', dest='user', action='store', \
    help='User for authentication, if different than repository owner.')

parser.add_option('-l', '--label', dest='extra_labels', action='append', default=[], \
    help='Add an extra label to apply to all issues.')

parser.add_option('-t', '--label-translation', dest='label_translation', action='store', \
    help='Label translation file.')

parser.add_option('-c', '--closing-status', dest='closed_statuses', action='append', \
    help='Add a status to the list of SF statuses that will close the issue.')

parser.add_option('-s', '--start', dest='start_id', action='store', type="int", default=-1, \
    help='ID of the first ticket to migrate; useful for aborted runs.')

parser.add_option('-m', '--max', dest='max_count', action='store', type="int", default=-1, \
    help='Maximum amount of tickets to process.')

parser.add_option('--no-create-labels', action="store_false", dest="create_labels", default=True, \
    help="Assume all required labels are already created in the github repository, and don't try to create them.")

parser.add_option('--dry-run', action="store_true", dest="dry_run", default=False, \
    help="Only print what would be done, and actually do nothing.")

opts, args = parser.parse_args()

try:
    input_file, tracker_name, full_repo = args
except (ValueError, IndexError):
    parser.print_help()
    sys.exit(1)

# get user for authentication either from options, or from repository
if opts.user is None:
    user = full_repo.split('/')[0]
else:
    user = opts.user

# load label translation, if given

label_translation = None
if opts.label_translation is not None:
    print "-- Loading translation..."
    fd = open(opts.label_translation)
    data = fd.read()
    label_translation = json.loads(data)
    print "-- Done."

# init closing statuses, if not given
if opts.closed_statuses is None:
    opts.closed_statuses = ["closed", "deleted"]

verbose = False

print "\n############### options:"
print "-- github repository:", full_repo
if opts.user is not None:
    print "-- github user:", user
print "-- extra labels:", opts.extra_labels
print "-- closing statuses:", opts.closed_statuses
print "-- start at ticket:", opts.start_id
print "-- max tickets:", opts.max_count
print "-- create labels:", opts.create_labels
print "-- dry run:", opts.dry_run

# globals #

github_url = "https://api.github.com"
issues_url = github_url + ("/repos/%s/issues" % full_repo)
labels_url = github_url + ("/repos/%s/labels" % full_repo)

proc_count = 0

# # # # #

def userInput(words, prompt=""):
    while True:
        s = raw_input((prompt + " ").lstrip() + "[" + ", ".join(words) + "] ? ")
        if s in words: return s
        print "Error: '" + s + "' unknown, please try again"

def userVerify(txt, abortOnFail=True):
    if userInput(["yes","no"], txt) != 'yes':
        if abortOnFail:
            print "Aborted."
            sys.exit(1)
        return False
    return True

def labelify(string):
    x = string
    x = re.sub(r'[^\w._()]+', ' ', x)
    x = x.rstrip(' ')
    return x

def pretty_print( json_string ):
    print json.dumps( json.loads(json_string), indent=3 )

def print_translations(tr_map):
    print "-- label translation:"

    print "categories:"
    cats = tr_map["categories"].items()
    for item in cats:
        string = "    [%s] %s ->" % (item[0], item[1][0])
        for tr in item[1][1]:
            string += " '%s'" % tr
        print string

    print "groups:"
    grps = tr_map["groups"].items()
    for item in grps:
        string = "    [%s] %s ->" % (item[0], item[1][0])
        for tr in item[1][1]:
            string += " '%s'" % tr
        print string

    labels = tr_map["labels"]
    print "labels to create:"
    for l in labels:
        print "    " + l

def prettify_body(body):
    body = re.sub(r'^Logged In: (NO|YES)\s*\n', '', body)
    body = re.sub(r'^user_id=\d+\n', '', body)
    body = re.sub(r'^Originator: (NO|YES)\n', '', body)
    return body

def handleError(response):
    #if response.status_code != requests.codes.ok:
        #pprint.pprint(response.headers)
        #if response.text is not None:
            #pprint.pprint( json.loads(response.text) )
    try:
        response.raise_for_status()
    except:
        print "ERROR:"
        pprint.pprint(response.headers)
        if response.text is not None:
            pretty_print( response.text )
        raise

    return response

def createIssue(issue):
    i = json.dumps(issue)
    if verbose:
        print i
    o = handleError( session.post(issues_url, data=i) )
    return json.loads(o.text)

def closeIssue(num):
    url = issues_url + "/" + str(num)
    o = handleError( session.patch(url, data='{"state": "closed"}') )
    return json.loads(o.text)

def createComment(issue_num, comment):
    url = issues_url + "/%d/comments" % issue_num
    i = json.dumps(comment)
    o = handleError( session.post(url, data=i) )
    return json.loads(o.text)

def createLabel(label):
    i = json.dumps(label)
    o = session.post(labels_url, data=i)
    r = json.loads(o.text)
    if o.status_code != 201:
        if r["errors"][0]["code"] == "already_exists":
            print ".. Label already exists."
        else:
            handleError(o)
    return r

def try_all():
    print "Creating issue..."
    issue = createIssue( {2: "even easier7"} )
    num = issue["number"]
    print "Created issue %d." % num

    print "Creating comment..."
    createComment(num, { "body": "I object!" } )
    print "Created comment."

    print "Closing issue..."
    closeIssue(num)
    print "Closed issue."


def handleComment( num, comment, issue_num ):
    id = comment.id.string
    print "\n--------------- start comment", id, num
    submitter = comment.submitter.string
    body = "[Comment migrated from SourceForge | Submitted by '%s']\n\n%s" \
        % (submitter, prettify_body(comment.details.string))
    print "-- submitter:", submitter
    print "-- body:", body[0:400].replace('\n', ' ') + "..."

    if not opts.dry_run:
        createComment( issue_num, { "body": body } )

    print "--------------- end comment:", id, num

def handleTicket( num, ticket, tr_map, closed_status_ids):
    global proc_count

    id = ticket.id.string
    if int(id) < opts.start_id:
        print "\n############### skipping ticket:", id, num
        return

    proc_count = proc_count + 1

    print "\n############### start ticket:", id, num, "- [%d/%d]" % (proc_count, opts.max_count)

    try:
        cat = tr_map["categories"][ticket.category_id.string][1]
    except KeyError:
        cat = []

    try:
        group = tr_map["groups"][ticket.group_id.string][1]
    except KeyError:
        group = []

    status_id = ticket.status_id.string
    submitter = ticket.submitter.string

    labels = []
    for l in cat:
        if l not in labels:
            labels.append(l)
    for l in group:
        if l not in labels:
            labels.append(l)
    for l in opts.extra_labels:
        if l not in labels:
            labels.append(l)

    title = ticket.summary.string

    body = "[Issue migrated from SourceForge | ID: %s | Submitted by '%s']\n[%s]\n\n%s" \
        % (id, submitter, ticket.url.string, prettify_body(ticket.details.string))

    closed = status_id in closed_status_ids

    print "-- title:", title
    print "-- labels:", labels
    print "-- closed:", closed
    print "-- body:", body[0:400].replace('\n', ' ') + "..."

    print "\n-- Creating issue..."
    if not opts.dry_run:
        response = createIssue( { "title": title, "body": body, "labels": labels } )
        issue_num = response['number']
    else:
        issue_num = "0"
    print "-- Done."

    if closed:
        print "\n-- Closing issue..."
        if not opts.dry_run:
            closeIssue(issue_num)
        print "-- Done."

    comments = ticket.followups('followup', recursive=False)
    comment_count = len(comments)
    i = 0
    for comment in comments:
        i = i + 1
        c_num = "[%d/%d]" % (i, comment_count)
        handleComment( c_num, comment, issue_num )

    print "\n############### end ticket:", id, num

    if proc_count == opts.max_count:
        print "\n-- Reached maximum amount of tickets to process. Aborting."
        sys.exit(0)

def resolveTranslations(tracker):
    cat_tr = {}
    grp_tr = {}
    if label_translation is not None:
        cat_tr = label_translation["categories"]
        grp_tr = label_translation["groups"]

    cat_map = {}
    grp_map = {}
    labels = []

    def store_tr(item, id, src, dst, labels):
        # resolve translation, and make sure it's a list
        if item in src:
            trs = src[item]
            if not isinstance(trs, list):
                trs = [trs]
        else:
            trs = [item]

        # store among all labels, weeding out Nones
        trs2 = []
        for tr in trs:
            if tr is not None:
                trs2.append(tr)
                if tr not in labels:
                    labels.append(tr)

        # store in the map
        dst[id] = (item, trs2)

    for category in tracker.categories('category', recursive=False):
        item = category.category_name.string
        id = category.id.string
        store_tr( item, id, cat_tr, cat_map, labels )


    for group in tracker.groups('group', recursive=False):
        item = group.group_name.string
        id = group.id.string
        store_tr( item, id, grp_tr, grp_map, labels )

    for l in opts.extra_labels:
        if l not in labels:
            labels.append(l)

    return { "categories": cat_map, "groups": grp_map, "labels": labels }

def handleTracker(tracker):
    global pwd, session

    tracker_name = tracker.find("name").string
    print "\n############### tracker: " + tracker_name

    tr_map = resolveTranslations(tracker)
    print_translations(tr_map)

    statuses = []
    closed_status_ids = []
    for status in tracker.statuses('status', recursive=False):
        status_name = status.find('name').string
        statuses.append(status_name)
        if status_name.lower() in opts.closed_statuses:
            closed_status_ids.append(status.id.string)
    print "-- statuses:", statuses

    tickets = tracker.tracker_items('tracker_item', recursive=False)
    ticket_count = len(tickets)
    print "-- ticket count:", ticket_count

    print "\n"

    userVerify("Shall we continue?")
    pwd = getpass('%s\'s GitHub password: ' % user)

    # start a Requests session

    session = requests.session( auth=(user, pwd), prefetch=True )

    if opts.create_labels:
        labels = tr_map["labels"]
        label_count = len(labels)
        i = 0
        for label in labels:
            i = i + 1
            print "\n-- Creating label '%s' [%d/%d] ..." % (label, i, label_count)
            if not opts.dry_run:
                createLabel( {"name": label, "color": "000000"} )
            print "-- Done."

    i = 0
    for ticket in tickets:
        i = i + 1
        num = "[%d/%d]" % (i, ticket_count)
        handleTicket(num, ticket, tr_map, closed_status_ids)

def process_tracker(tracker_name):
    soup = BeautifulSoup(open(input_file), "xml")

    def match_tracker(tracker):
        return tracker.name == "tracker" and tracker.find("name").string == tracker_name

    tracker = soup.document.find("trackers", recursive=False).find(match_tracker, recursive=False)

    if tracker is None:
        print "Could not find tracker '%s'" % tracker_name
        sys.exit(1)

    #trackers = soup.document.find("trackers", recursive=False).findAll("tracker", recursive=False)

    handleTracker(tracker)

#createIssue( {"title": "what a title", "body": "", "labels": ["1.2"]} )

process_tracker(tracker_name)

print "\nDone."
