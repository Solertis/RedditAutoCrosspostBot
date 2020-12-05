"""Retrieves aged comment entries from the DB and checks whether they pass required filters to be crossposted, but does not crosspost them yet.
The objective is to reduce database space usage"""

import logging
import os
import concurrent.futures

import praw
import pytimeparse

import racb_db
import reddit_instantiator
import repost_detector
import phase1_handler

def filter_comments_from_db():
    logging.info('Running phase 2 comment filter')
    PHASE2_WAITING_PERIOD = os.environ.get('PHASE2_WAITING_PERIOD')
    waiting_period_seconds = pytimeparse.timeparse.timeparse(PHASE2_WAITING_PERIOD)
    comment_entries = racb_db.get_unchecked_comments_older_than(waiting_period_seconds)
    logging.info(f'Found {len(comment_entries)} unchecked comments')
    if len(comment_entries) == 0:
        return
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        executor.map(process_comment_entry, comment_entries)
    logging.info('Finished running phase 2 comment filter')


def run_filters(comment_entry):
    class Result:
        passes_filter = False
        reason = None
        comment = None
        target_subreddit = target_subreddit
        post_with_same_content = None

    result = Result()

    comment = get_full_comment_from_reddit(comment_entry['permalink'])
    result.comment = comment
    available = check_comment_availability(comment)

    if not available:
        result.reason = 'COMMENT_UNAVAILABLE'
        return result

    comment_score_threshold = int(os.environ.get('COMMENT_SCORE_THRESHOLD'))
    if comment.score < comment_score_threshold:
        result.reason = 'COMMENT_SCORE_TOO_LOW'
        return result

    target_subreddit = phase1_handler.check_pattern(comment)
    result.target_subreddit = target_subreddit
    if target_subreddit is None:
        # this can happen when the source comment was edited since it was scraped
        result.reason = 'TARGET_SUBREDDIT_NOT_FOUND'
        return result

    gpwsc_result = phase1_handler.get_posts_with_same_content(comment, target_subreddit)
    if gpwsc_result.posts_found:
        result.reason = 'POST_WITH_SAME_CONTENT_FOUND'
        result.post_with_same_content = gpwsc_result.posts[0]
        return result
    elif gpwsc_result.unable_to_search:
        unable_to_search_reason = gpwsc_result.unable_to_search_reason
        result.reason = f'UNABLE_TO_SEARCH_TARGET_SUBREDDIT_BECAUSE__{unable_to_search_reason}'
        return result
    
    result.passes_filter = True
    return result

def get_full_comment_from_reddit(permalink_without_prefix):
    reddit = reddit_instantiator.get_reddit_instance()
    return reddit.comment(url=r'https://www.reddit.com' + permalink_without_prefix)


def check_comment_availability(comment):
    try:
        comment.score  # access a property to trigger praw to retrieve the comment
        return True
    except Exception as e:
        if isinstance(e, praw.exceptions.ClientException):
            logging.info('This comment cannot be reached for some reason (maybe removed or banned)')
            return False
        else:
            raise


def process_comment_entry(comment_entry):
    result = run_filters(comment_entry)
    passed = result != False
    if passed:
        racb_db.set_comment_checked(comment_entry)
    else:
        racb_db.delete_comment(comment_entry)