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

parser.add_option('-l', '--label', dest='extra_labels', action='append', \
    help='Add an extra label to apply to all issues.')

parser.add_option('-c', '--closing-status', dest='closed_statuses', action='append', \
    help='Add a status to the list of SF statuses that will close the issue.')

parser.add_option('-s', '--start', dest='start_id', action='store', type="int", default=-1, \
    help='ID of the first ticket to migrate; useful for aborted runs.')

parser.add_option('--no-create-labels', action="store_false", dest="create_labels", default=True, \
    help="Assume all required labels are already created in the github repository, and don't try to create them.")

parser.add_option('--dry-run', action="store_true", dest="dry_run", default=False, \
    help="Only print what would be done, and actually do nothing.")

opts, args = parser.parse_args()

try:
    input_file, tracker_name, full_repo = args
    if opts.user is None:
        user = full_repo.split('/')[0]
    else:
        user = opts.user
except (ValueError, IndexError):
    parser.print_help()
    sys.exit(1)

if opts.closed_statuses is None:
    opts.closed_statuses = ["closed", "deleted"]

verbose = False

# globals #

github_url = "https://api.github.com"
issues_url = github_url + ("/repos/%s/issues" % full_repo)
labels_url = github_url + ("/repos/%s/labels" % full_repo)

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
    x = string.lower()
    x = re.sub(r'[^\w._()]+', ' ', x)
    x = x.rstrip(' ')
    return x

def pretty_print( json_string ):
    print json.dumps( json.loads(json_string), indent=3 )

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
    o = handleError( requests.post(issues_url, auth=(user, pwd), data=i) )
    return json.loads(o.text)

def closeIssue(num):
    url = issues_url + "/" + str(num)
    o = handleError( requests.patch(url, auth=(user, pwd), data='{"state": "closed"}') )
    return json.loads(o.text)

def createComment(issue_num, comment):
    url = issues_url + "/%d/comments" % issue_num
    i = json.dumps(comment)
    o = handleError( requests.post(url, auth=(user, pwd), data=i) )
    return json.loads(o.text)

def createLabel(label):
    i = json.dumps(label)
    o = requests.post(labels_url, auth=(user, pwd), data=i)
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
    body = "[Comment migrated from SourceForge, submitted by '%s'.]\n\n%s" % (submitter, comment.details.string)
    print "-- submitter:", submitter
    print "-- body:", body[0:400].replace('\n', ' ') + "..."

    if not opts.dry_run:
        createComment( issue_num, { "body": body } )

    print "--------------- end comment:", id, num

def handleTicket( num, ticket, categories, groups, closed_status_ids, extra_labels ):
    id = ticket.id.string
    if int(id) < opts.start_id:
        print "\n############### skipping ticket:", id, num
        return

    cat = categories[ticket.category_id.string]
    group = groups[ticket.group_id.string]
    status_id = ticket.status_id.string
    submitter = ticket.submitter.string

    print "\n############### start ticket:", id, num

    labels = [cat, group]
    if extra_labels is not None:
        for l in extra_labels:
            labels.append( l )

    title = ticket.summary.string

    body = "[Issue migrated from SourceForge, id '%s', submitted by '%s'.]\n[%s]\n\n%s" \
        % (id, submitter, ticket.url.string, ticket.details.string)

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
        num = "[%d/%d]" % (i, comment_count)
        handleComment( num, comment, issue_num )

    print "\n############### end ticket:", id, num

def handleTracker(tracker, extra_labels=None):
    global pwd

    tracker_name = tracker.find("name").string
    print "\n############### tracker: " + tracker_name

    labels = []

    categories = {}
    for category in tracker.categories('category', recursive=False):
        label = labelify(category.category_name.string)
        labels.append(label)
        categories[category.id.string] = label
    print "-- categories:", categories

    groups = {}
    for group in tracker.groups('group', recursive=False):
        label = labelify(group.group_name.string)
        labels.append(label)
        groups[group.id.string] = label
    print "-- groups:", groups

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

    print "\n############### options:"
    print "-- github repository:", full_repo
    if opts.user is not None:
        print "-- github user:", user
    print "-- extra labels:", extra_labels
    print "-- closing statuses:", opts.closed_statuses
    print "-- start at ticket:", opts.start_id
    print "-- create labels:", opts.create_labels
    print "-- dry run:", opts.dry_run
    print "\n"

    userVerify("Shall we continue?")
    pwd = getpass('%s\'s GitHub password: ' % user)

    if opts.create_labels:
        i = 0
        label_count = len(labels)
        for label in labels:
            i = i + 1
            print "\n-- Creating label '%s' [%d/%d] ..." % (label, i, label_count)
            if not opts.dry_run:
                createLabel( {"name": label, "color": "FFFFFF"} )
            print "-- Done."

    i = 0
    for ticket in tickets:
        i = i + 1
        num = "[%d/%d]" % (i, ticket_count)
        handleTicket(num, ticket, categories, groups, closed_status_ids, extra_labels)

def process_tracker(tracker_name):
    soup = BeautifulSoup(open(input_file), "xml")

    def match_tracker(tracker):
        return tracker.name == "tracker" and tracker.find("name").string == tracker_name

    tracker = soup.document.find("trackers", recursive=False).find(match_tracker, recursive=False)

    if tracker is None:
        print "Could not find tracker '%s'" % tracker_name
        sys.exit(1)

    #trackers = soup.document.find("trackers", recursive=False).findAll("tracker", recursive=False)

    handleTracker(tracker, opts.extra_labels)

#createIssue( {"title": "what a title", "body": "", "labels": ["1.2"]} )

process_tracker(tracker_name)

print "\nDone."
