"""Listens to incoming comments from reddit, checks if they match the 'subreddit recommendation' pattern, and saves them to the DB"""

import logging
import re
import json

import consts
import racb_db
import reddit_instantiator
import my_i18n as i18n
import repost_detector

SUBREDDIT_NAME_LENGTH_LIMIT = 24

def handle_incoming_comment(comment):
    logging.debug(f'Handling a comment: {comment.permalink}')
    target_subreddit = check_pattern(comment)
    if target_subreddit is None:
        return

    if len(target_subreddit) > SUBREDDIT_NAME_LENGTH_LIMIT:
        return

    if is_mod_post(comment):
        return
        
    if title_contains_prohibited_phrases(comment):
        return

    source_subreddit = comment.subreddit.display_name.lower()
    if target_subreddit.lower() in consts.SUB_BLACKLIST or source_subreddit in consts.SUB_BLACKLIST:
        return
    
    if target_subreddit.lower() == source_subreddit:
        logging.info('Found "source_subreddit=target_subreddt" comment. Replying to source comment.')
        reply_to_source_equals_target_comment(comment, target_subreddit)
        return

    result_obj = get_posts_with_same_content(comment, target_subreddit)
    if result_obj.posts_found:
        logging.info('Found post with same content. Replying to source comment.')
        post_with_same_content=result_obj.posts[0] 
        reply_to_same_content_post_comment(comment, target_subreddit, post_with_same_content)
        return
    elif result_obj.unable_to_search and result_obj.unable_to_search_reason == 'SUBREDDIT_DOES_NOT_EXIST':
        logging.info('Found a reference to a subreddit that does not exist. Replying to source comment.')
        reply_to_nonexistent_target_subreddit_comment(comment, target_subreddit)
        return

    if not is_top_level_comment(comment):
        return

    logging.info(f'Match found: {comment.permalink}')
    racb_db.add_comment(comment)


def is_mod_post(comment):
    return comment.distinguished == 'moderator'


def is_top_level_comment(comment):
    return comment.parent_id == comment.link_id
    
prohibited_phrases = ['sub', 'subs', 'subreddit', 'subreddits']
prohibited_phrases = [(r'\b' + x + r'\b') for x in prohibited_phrases]
prohibited_phrases = '|'.join(prohibited_phrases)
prohibited_phrases = re.compile(prohibited_phrases, flags=re.IGNORECASE)

def title_contains_prohibited_phrases(comment):
    title = comment.submission.title
    return prohibited_phrases.search(title) is not None

subreddit_regex = re.compile(r'^(/)?r/([a-zA-Z0-9-_]+)$')  # compile once

def check_pattern(comment):
    '''Checks if the comment's body contains only a reference to a subreddit,
    and return the subreddit name if there's a match
    '''
    search_result = subreddit_regex.search(comment.body)
    if search_result is None:
        return None

    groups = search_result.groups()

    target_subreddit = groups[1]
    return target_subreddit

def get_posts_with_same_content(comment, subreddit):
    class Result:
        posts_found =False
        posts = []
        unable_to_search = False
        unable_to_search_reason = None

    result = Result()

    reddit = reddit_instantiator.get_reddit_instance()
    try:
        # The format of the query string is explained here: https://github.com/praw-dev/praw/issues/880
        query = f'url:\"{comment.submission.url}\"'
        submissions = reddit.subreddit(subreddit).search(query=query, sort='new', time_filter='all')
        # iterate over submissions to fetch them
        submissions = [s for s in submissions]
    except Exception as e: #TODO change exception type to be specific
        error_message = e.args[0]
        # when reddit tries redirecting a search query of a link to the submission page, that means 0 results were found for the search query
        if error_message == 'Redirect to /submit':
            return result
        # when reddit redirects to /subreddits/search that means the subreddit doesn't exist
        elif error_message in ['Redirect to /subreddits/search', 'received 404 HTTP response']:
            if e.response.text:
                try:
                    response_obj = json.loads(e.response.text)
                    if response_obj['reason'] == 'banned':
                        result.unable_to_search = True
                        result.unable_to_search_reason = 'SUBREDDIT_IS_BANNED'
                        return result
                except json.JSONDecodeError:
                    pass
            result.unable_to_search = True
            result.unable_to_search_reason = 'SUBREDDIT_DOES_NOT_EXIST'
            return result
        # this error is recieved when the subreddit is private
        # "You must be invited to visit this community"
        elif error_message == 'received 403 HTTP response':
            result.unable_to_search = True
            result.unable_to_search_reason = 'SUBREDDIT_IS_PRIVATE'
            return result
        else:
            raise

    if len(submissions) > 0:
        result.posts_found = True
        result.posts = submissions
        return result

    prior_posts = repost_detector.get_reposts_in_sub(comment, subreddit)
    if prior_posts:
        result.posts_found = True
        result.posts = prior_posts #TODO maybe transform the post object into a praw-wrapped submission object?

    return result

def reply_to_source_equals_target_comment(source_comment, target_subreddit):
    text = i18n.get_translated_string('THATS_WHERE_WE_ARE', target_subreddit)
    reddit_SSB = reddit_instantiator.get_reddit_instance(username = reddit_instantiator.SAME_SUBREDDIT_BOT_NAME)
    source_comment_SSB = reddit_SSB.comment(id=source_comment.id)
    source_comment_SSB.reply(text)
    return

def reply_to_nonexistent_target_subreddit_comment(source_comment, target_subreddit):
    text = i18n.get_translated_string('NONEXISTENT_SUBREDDIT', target_subreddit)
    text = text.format(target_subreddit=target_subreddit,)
    reddit_SDE = reddit_instantiator.get_reddit_instance(username = reddit_instantiator.SUB_DOESNT_EXIST_BOT_NAME)
    source_comment_SDE = reddit_SDE.comment(id=source_comment.id)
    source_comment_SDE.reply(text)
    return

# TODO: make up a better name for this function
def reply_to_same_content_post_comment(source_comment, target_subreddit, post_with_same_content):
    text = i18n.get_translated_string('FOUND_POST_WITH_SAME_CONTENT', target_subreddit)
    text = text.format(same_content_post_url=post_with_same_content.permalink,
                       target_subreddit=target_subreddit,)
    source_comment.reply(text)
    return