from oauth2client.client import OAuth2WebServerFlow
from oauth2client.client import flow_from_clientsecrets
from googleapiclient.discovery import build
from bottle import get, post, route, run, request, redirect, app, template
from bottle import static_file, Response, response
import bottle
from beaker.middleware import SessionMiddleware
from heapq import *
import collections
import httplib2
import Queue
from pymongo import *
from math import ceil
from bottle import error


#global data structures
session_opts = {
    'session.type': 'file',
    'session.cookie_expires': 300,
    'session.data_dir': './data',
    'session.auto': True
}
app = SessionMiddleware(app(), session_opts)

user_most_recent_dict = {}
user_history_dict = {}
user_history_heap = {}

user_most_recent_dict = {}


client = MongoClient("localhost", 27017)

# crawler_db stores the crawler data
crawler_db = client["crawler"]

# user_db stores user search result
user_db = client["user"]

#load crawler data from database

lexicon = crawler_db["crawler"].find_one({"type": "lexicon"})
inverted_index = crawler_db["crawler"].find_one({"type": "inverted_index"})
pg_scores = crawler_db["crawler"].find_one({"type": "pg_score"})
doc_id_to_url = crawler_db["crawler"].find_one({"type": "doc_id_to_url"})
doc_index = crawler_db["crawler"].find_one({"type": "doc_index"})

if not (lexicon and inverted_index and pg_scores and doc_id_to_url and doc_index):
        print "warning: crawler data loadding is incomplete!"
else:
        print "crawler data loaded!"

SCOPE = ['https://www.googleapis.com/auth/plus.me', 'https://www.googleapis.com/auth/userinfo.email']

#note page starting at 1 for easy-to-readness
PAGE_SIZE = 5

@route('/')
def search_page():
    s = request.environ.get('beaker.session')
    #response.set_header("Cache-Control", "no-cache, no-store, must-revalidate")
    inputString = request.query.keywords
    if 'email' in s: #user logged in
        logged_in = True

        if inputString == "":
            return template('frontend.tpl', loggedin=logged_in, name=s['name'], email=s['email'])
        else:
            return search_result(inputString)
    else:
        if inputString == "":
            return template('frontend.tpl', loggedin=False)
        else:
        	inputString = inputString.split()
        	redirect('/&keywords=' + inputString[0] + '&page_no=1')

@route('/login')
def login_trigger():

    s = request.environ.get('beaker.session')
    if 'email' not in s:
        flow = flow_from_clientsecrets('./client_secret.json', scope=SCOPE, redirect_uri="http://ec2-52-90-64-161.compute-1.amazonaws.com//redirect")
        auth_uri = flow.step1_get_authorize_url()
        redirect(str(auth_uri))
    else:
        redirect(str('/'))

@route('/logout')
def logout_trigger():
    session = request.environ.get('beaker.session')
    session.delete()
    redirect(str("/"))

@route('/redirect')
def redirect_page():
    code = request.query.get('code','')

    flow = OAuth2WebServerFlow(client_id='619195777450-ea3m50l60rlmbo9ro0abiimmb4o9admp.apps.googleusercontent.com',
                            client_secret='SBurZL_VZPCjaLLVEKGRyD5v',
                            scope=SCOPE,
                            redirect_uri="http://ec2-52-90-64-161.compute-1.amazonaws.com//redirect")

    credentials = flow.step2_exchange(code)
    token = credentials.id_token['sub']
    http = httplib2.Http()
    http = credentials.authorize(http)

    users_service = build('oauth2', 'v2', http=http)
    user_document = users_service.userinfo().get().execute()
    user_email = user_document['email']
    print user_document

    session = request.environ.get('beaker.session')
    session['email'] = user_email
    session['name'] = user_document['given_name']
    session['picture'] = user_document['picture']
    session['logged_in'] = True
    session.save()
    redirect(str('/'))

@get('/&keywords=<keywords>&page_no=<page>')
def search_result(keywords, page):
	page = int(page)
	keywords = keywords.lower()
	URLs, length = db_query(keywords, "default", page)
	print URLs
	if not URLs and page == 1:
		return template('search_results.tpl', URLs=URLs, result=False)
	if page > ceil(float(length)/PAGE_SIZE):
		return template('error.tpl')

	#get the titles of each URL, need to preload it
	titles = []
	for url in URLs:
		for i in range(len(doc_index["value"])):
			if doc_index["value"][i]["url"] == url:
				if doc_index["value"][i]["title"]:
					titles.append(doc_index["value"][i]["title"])
				else:
					titles.append(url)
	print titles
	return template('search_results.tpl', titles=titles, URLs=URLs, result=True, keywords=keywords, page=page, length=ceil(float(length)/PAGE_SIZE))

@route('/static/<filename>')
def server_static(filename):
    return static_file(filename, root='./static')

def search_table(inputString):
    bottle.TEMPLATES.clear()
    s = request.environ.get('beaker.session')
    response.set_header("Cache-Control", "no-cache, no-store, must-revalidate")
    result, num = db_query(inputString)
    print result
    
    search_result_title = "<p> Search for \"" + inputString + "\" </p>"

    inputStringLower = inputString.lower()
    splitInput = inputStringLower.split();


    #The following creates a dictionary that stores the count of the occurences
    #of each word IN THE ORDER in which they appear
    occurence_dict = collections.OrderedDict()
    for word in splitInput:
        if word in occurence_dict:
            occurence_dict[word] += 1
        else:
            occurence_dict[word] = 1

        
    if 'logged_in' not in s:
        return template('results.tpl', logged_in=False,inputString=inputStringLower, splitInput=splitInput, occurence_dict=occurence_dict)
    #count the words in a dictionary and put it in the min heap if it's top 20

    name = s['name']
    email = s['email']
    if email not in user_history_dict and email not in user_history_heap and email not in user_most_recent_dict:
        user_history_dict[email] = {}
        user_history_heap[email] = []
        user_most_recent_dict[email] = []

    if len(user_most_recent_dict[email]) < 10:
        user_most_recent_dict[email].append(inputString)
    else:
        user_most_recent_dict[email].pop(0)
        user_most_recent_dict[email].append(inputString)

    user_history_dict[s['email']], user_history_heap[s['email']] = insert_into_dict_and_heap(user_history_dict[s['email']], user_history_heap[s['email']], splitInput)
    copy_heap = sorted(list(user_history_heap[s['email']]))
    reversed_copy_heap = reversed(copy_heap)


    return template('results.tpl', logged_in=True, name=name, inputString=inputStringLower, splitInput=splitInput, occurence_dict=occurence_dict, reversed_copy_heap=reversed_copy_heap, queue=user_most_recent_dict[email])

def insert_into_dict_and_heap(user_dict, min_heap, word_list):
    for word in word_list:
        if word in user_dict:
            user_dict[word] += 1
        else:
            user_dict[word] = 1

        word_in_heap = False #flag to see if the word is already in the heap

        #check to see if the word is already in the heap
        for i in range(0, len(min_heap)):
            if min_heap[i][1] == word:
                min_heap[i][0] = min_heap[i][0] + 1
                word_in_heap = True
            
        heapify(min_heap)

        #add the word and its count into the heap if its in the top 20; if heap less than 20 entries, then insert it automatically
        if not word_in_heap:
            if len(min_heap) < 20:
                heappush(min_heap, [user_dict[word], word])
                heapify(min_heap)
            elif min_heap[0][0] < user_dict[word]:
                heappop(min_heap)
                heappush(min_heap, [user_dict[word], word])		
                heapify(min_heap)
    return user_dict, min_heap

def find_urls(query_str):
	result = []
	if query_str not in lexicon["value"]:
		return result
	word_id = lexicon["value"][query_str]
	doc_ids = inverted_index["value"][str(word_id)]
	doc_pgscore = {}
	for d_id in doc_ids:
		doc_pgscore[d_id] = pg_scores["value"][str(d_id)]
    #get the urls from the sorted doc_ids based on pg_score     
	
	for d_id in sorted(doc_pgscore, key=doc_pgscore.get):
		result.append(doc_id_to_url["value"][str(d_id)])
		print "query string is: " + query_str 
	#print result
	return result

def db_query(query_str, user="default", page_num=1):
		if page_num < 1:
			page_num = 1
		user_document = user_db[str(user)].find_one({"type":"search_result"})
        #update the db if needed
		if user_document == None:
			user_db[str(user)].insert_one({"type":"search_result", "search_word":str(query_str), "result": find_urls(query_str)})
		elif user_document["search_word"] != query_str:
			user_db[str(user)].replace_one({"type":"search_result"}, {"type":"search_result", "search_word":str(query_str), "result": find_urls(query_str)})
        
        #get all result
		result = user_db[str(user)].find_one({"type":"search_result"})["result"]
		result_length = len(result)
        #return the data in the page
		return result[PAGE_SIZE*(page_num - 1): PAGE_SIZE*(page_num - 1) + PAGE_SIZE], result_length
            
'''
these are some test cases, don't delete. we can use them to test db_query function
    #db_query("10", "4324324")
    db_query("can", "4324324")
    print db_query("10", "4324324", 2)
'''

@error(404)
def error404(error):
    return template('error.tpl')


run(host='0.0.0.0', port=8081, debug=True, app=app)