"""Bound and ground photo choices for the ordinary dialogue turn."""

import re
from datetime import date, timedelta

from django.db.models import F, Q
from django.utils import timezone

from world.post_links import current_previous


PHOTO_WORD = re.compile(r'사진|이미지|짤|photo|picture|pic\b', re.IGNORECASE)
PHOTO_CAPTURE = re.compile(r'찍(?:은|었|힌)')
REQUEST_ENDING = r'(?:줘|봐|주(?:라|세요|실|면|길)|줄\s*(?:래|까|수))'
SHARE_REQUEST = re.compile(
    rf'(?:(?:보여|보내|올려|(?:재)?전송해)\s*{REQUEST_ENDING}|'
    r'보고\s*싶|볼\s*수\s*있(?:을까|어\?|나요)|send\b|show\s+me\b|can i see\b|resend\b)', re.IGNORECASE,
)
REPEAT_REQUEST = re.compile(r'다시|한\s*번\s*더|또\s*(?:보여|보내)|재전송|again|resend', re.IGNORECASE)
POINTED_PHOTO = re.compile(
    r'(?<![가-힣])[이저그]\s*(?:사진|거)|보내\s*준\s*(?:사진|거)|\bthat\s+(?:photo|picture|pic)\b', re.IGNORECASE,
)
PHOTO_REFERENCE = re.compile(
    POINTED_PHOTO.pattern + r'|(?<![가-힣])그\s*(?:때|건|게|애|고양이|강아지)|아까|방금', re.IGNORECASE,
)
PHOTO_QUESTION = re.compile(r'찍|사진|이름|무슨\s*색')
NAMED_PHOTO = {
    'cat': re.compile(r'고양이|냥이|두부|\bcat\b|\bkitten\b', re.IGNORECASE),
    'dog': re.compile(r'강아지|댕댕이|반려견|\bdog\b|\bpuppy\b', re.IGNORECASE),
    'receipt': re.compile(r'영수증|진료비\s*내역|\breceipt\b', re.IGNORECASE),
}
DOCUMENT = re.compile(r'영수증|진료비\s*내역|안내문|\breceipt\b', re.IGNORECASE)
INSET = re.compile(r'그림|일러스트|아이콘|로고|캐릭터|인형|스티커|삽화|illustration|logo|sticker', re.IGNORECASE)
PHOTO_NOUN = re.compile(r'[가-힣A-Za-z0-9]+')
GENERIC_PHOTO = re.compile(
    r'^(?:사진|이미지|짤)(?:을|이|도|은|는)?\s*(?:한\s*장\s*)?$|'
    r'^(?:아무|아무거나|최근|요즘|새|새로운|다른|최신|오늘|어제|방금)(?:\s*찍은)?\s*(?:사진|이미지|짤)(?:을|이|도|은|는)?$|'
    r'^(?:(?:오늘|어제|방금)\s*)?찍은\s*거(?:를)?$'
)
EXPLICIT_DAY = re.compile(r'(?:(\d{4})[-년]\s*)?(\d{1,2})[-월]\s*(\d{1,2})(?:일|\b)')
PRIOR_SHARE = re.compile(
    r'(?:오늘|어제|그제|그저께|\d{1,2}[-월]\s*\d{1,2}일?)\s*(?:네가\s*)?'
    r'(?:보내\s*(?:준|줬던|줬었|줬|주신)|보낸|공유한|받은)|'
    r'(?:you sent(?: me)?|you shared|sent me)\s+(?:today|yesterday)', re.IGNORECASE,
)
RECENT_PHOTO_WINDOW = timedelta(days=2)
PHOTO_LIMIT = 4
RECENT_QUOTE_WINDOW = timedelta(days=7)
POST_LINK_KEY = 'post_link'
POST_TOPIC_PARTICLE = re.compile(r'(?:으로|에서|에게|까지|부터|이랑|하고|처럼|[은는이가을를의와과에로도])$')
POST_TOPIC_FILLER = set(
    '그 이 저 거 글 사진 게시물 피드 이번 저번 지난 오늘 어제 아까 지금 요즘 결국 나중 처음 '
    '진짜 너무 다시 근데 그때 거기 이제 최종 무슨 어떤 언제 어디 어떻게 뭐 뭐야 '
    '있어 있었어 좋아 했어 하는 같아 봤어 골랐어 결과'.split()
)
POST_TOPIC_FOLLOWUP = re.compile(
    r'(?:(?:근데|그럼|그래서|응|아|아니)[, ]+)*'
    r'(?:(?:그때|그거|그건|그게|그 일|그 사진|그 (?:뒤|다음|후)(?:에)?|'
    r'저번에|지난번에|결국|나중에|처음에|이어서)\s+)*'
    r'(?:(?:어떻게|뭐(?:가|로)?|잘|왜|언제|몇 시에|무슨 색(?:으로)?)\s*)?'
    r'(?:됐|됬|했|골랐|찍었|끝났|나왔|올렸|바꿨)(?:어|지|니|더라|던 거야|었지)?[?？.! ]*'
)
PUBLIC_POST_REFERENCE = re.compile(
    r'피드|게시물|올린\s*(?:글|사진)|올렸던\s*(?:글|사진)|\b(?:post|feed)\b', re.IGNORECASE,
)
POST_OUTCOME = re.compile(r'결국|그\s*(?:뒤|이후)|어떻게\s*(?:됐|되었)|마무리|끝났|(?:았|었|했)(?:어|니)\s*[?？]?\s*$')


def post_topic_words(text):
    words = (POST_TOPIC_PARTICLE.sub('', word.lower()) for word in PHOTO_NOUN.findall(text))
    return {word for word in words if len(word) >= 2 and word not in POST_TOPIC_FILLER}


def post_subject(post):
    return post_topic_words(' '.join((post.caption, post.photo_alt, *post.public_facts)))


def quote_continues_topic(question, subject, intervening):
    words = post_topic_words(question)
    if words & subject:
        return True
    if not POST_TOPIC_FOLLOWUP.fullmatch(question):
        return False
    # A bare “그거 어떻게 됐어?” points to the last conversation topic.
    return len(intervening) <= 16 and all(
        post_topic_words(text) & subject or POST_TOPIC_FOLLOWUP.fullmatch(text) for text in intervening
    )


def recent_post_quote(history, asked_at):
    return (history.filter(sender='user', source_post_id__isnull=False,
                           created_at__range=(asked_at - RECENT_QUOTE_WINDOW, asked_at))
            .order_by('-pk').first())


def post_link_is_current(versions, posts, now):
    """Check both pinned editions and their relationship in one loaded snapshot."""
    current_id, earlier_id = versions[POST_LINK_KEY]
    current, earlier = posts.get(str(current_id)), posts.get(str(earlier_id))
    if not current or not earlier or (
        current.version != versions.get(str(current_id))
        or earlier.version != versions.get(str(earlier_id))
        or current.previous_post_id != earlier.pk
    ):
        return False
    current.previous_post = earlier
    return current_previous(current, now) is not None


def photo_subject(text, *, visible=False):
    subjects = {subject for subject, pattern in NAMED_PHOTO.items() if pattern.search(text)}
    if visible and (DOCUMENT.search(text) or INSET.search(text)):
        subjects -= {'cat', 'dog'}
    return subjects


def sharing_requested(question):
    return bool(SHARE_REQUEST.search(question) and (
        PHOTO_WORD.search(question) or PHOTO_CAPTURE.search(question)
        or PHOTO_REFERENCE.search(question) or photo_subject(question)
        or REPEAT_REQUEST.search(question)
    ))


def repeat_requested(question):
    question = re.sub(r'다시\s*찍', '찍', question)
    return bool(sharing_requested(question) and REPEAT_REQUEST.search(question))


def requested_photo_day(question, asked_on):
    """Resolve the date of a requested photo against the user's message."""
    if not (sharing_requested(question) or PHOTO_WORD.search(question)):
        return None
    question = PRIOR_SHARE.sub('', question)
    explicit = EXPLICIT_DAY.search(question)
    if explicit:
        try:
            return date(int(explicit[1] or asked_on.year), int(explicit[2]), int(explicit[3]))
        except ValueError:
            return None
    # Travel and event plans can mention unrelated dates. A nearby photo or
    # capture verb makes the modifier apply to the picture itself.
    for word, offset in (('오늘|today', 0), ('어제|yesterday', -1), ('그제|그저께', -2), ('내일|tomorrow', 1)):
        if re.search(rf'(?:{word})(?:(?![,.?;!]).){{0,28}}(?:찍|사진|photo|picture|고양이|강아지)',
                     question, re.IGNORECASE) or re.search(rf'(?:photo|picture) from (?:{word})', question, re.IGNORECASE):
            return asked_on + timedelta(days=offset)
    return None


def describes_request(photo_alt, question):
    question = re.split(r'말고|대신', question)[-1]
    subjects = photo_subject(question)
    if DOCUMENT.search(question):
        return bool(DOCUMENT.search(photo_alt))
    if INSET.search(question):
        return bool(INSET.search(photo_alt)) and subjects <= photo_subject(photo_alt)
    return not subjects or subjects <= photo_subject(photo_alt, visible=True)


def photo_posts(public):
    return public.filter(photo__character_id=F('character_id')).exclude(photo__image='')


def related_reference(turns, history, public, question, asked_at):
    """Find the explicit quote or a related, visible photo this room received."""
    trigger = turns[0]
    quote = next((turn for turn in turns if turn.source_post_id), None)
    attachments = history.filter(sender='character', attachment_version__isnull=False).order_by('-pk')
    repeat = repeat_requested(question)

    if trigger.source_post_id:
        return trigger.source_post_id, trigger.source_version, True
    if repeat:
        # Include quotes outside the normal turn window, but only this room's
        # actual last reference. A withdrawn/stale reference cannot fall back.
        last = history.filter(source_post_id__isnull=False).order_by('-pk').first()
        sent = attachments.first()
        if sent and (last is None or sent.pk > last.pk):
            return sent.attachment_post_id, sent.attachment_version, False
        if last:
            return last.source_post_id, last.source_version, True
        return None

    named_subject = photo_subject(question)
    deictic = bool(PHOTO_REFERENCE.search(question))
    if deictic or named_subject or PHOTO_QUESTION.search(question):
        recent = attachments.filter(created_at__gte=asked_at - RECENT_PHOTO_WINDOW)
        if quote:
            recent = recent.filter(pk__gt=quote.pk)
        recent = list(recent[:PHOTO_LIMIT if named_subject and not deictic else 1])
        visible = {p.pk: p for p in photo_posts(public).filter(pk__in=[m.attachment_post_id for m in recent])}
        for sent in recent:
            if not deictic and not named_subject and sum(t.pk > sent.pk for t in turns[1:4]) > 1:
                break
            if named_subject:
                photo = visible.get(sent.attachment_post_id)
                if photo and photo.version == sent.attachment_version:
                    if not describes_request(photo.photo_alt, question):
                        continue
                elif not deictic:
                    continue
            return sent.attachment_post_id, sent.attachment_version, False
    if quote:
        return quote.source_post_id, quote.source_version, True
    return None


def photo_candidates(public, history, reference, question, asked_on, now, *, blocked_post_id=None):
    """Offer only fresh published photos or the exact photo requested again."""
    eligible = photo_posts(public).order_by('-published_at', '-pk')
    if repeat_requested(question):
        recent = [reference] if reference and eligible.filter(pk=reference.pk).exists() else []
    else:
        recent = list(eligible[:PHOTO_LIMIT])
        shown = set(history.filter(sender='character', attachment_post_id__in=[p.pk for p in recent])
                    .values_list('attachment_post_id', 'attachment_version'))
        recent = [p for p in recent if p.pk != blocked_post_id and (p.pk, p.version) not in shown]

    wanted_day = requested_photo_day(question, asked_on)
    if wanted_day:
        recent = [p for p in recent if p.event_at and p.event_at <= now
                  and timezone.localdate(p.event_at) == wanted_day]
    if sharing_requested(question):
        recent = [p for p in recent if describes_request(p.photo_alt, question)]
        if POINTED_PHOTO.search(question):
            recent = [p for p in recent if reference and p.pk == reference.pk]
    return recent


def sample_photo(context):
    """Make the fake backend share only for an explicit, identifiable request."""
    question = context['recent_turns'][-1]['text']
    if not sharing_requested(question):
        return None
    candidates = context.get('photo_candidates', [])
    target = re.split(r'말고|대신', question)[-1]
    phrase = re.sub(r'\s*좀$', '', SHARE_REQUEST.split(target, maxsplit=1)[0].strip())
    subject = PHOTO_WORD.split(phrase, maxsplit=1)[0].strip()
    if repeat_requested(question) or POINTED_PHOTO.search(target) or GENERIC_PHOTO.fullmatch(phrase):
        return next(iter(candidates), None)
    if photo_subject(target):
        return next((p for p in candidates if describes_request(p['photo_alt'], target)), None)
    words = {word for word in PHOTO_NOUN.findall(subject) if len(word) >= 2}
    return next((p for p in candidates if any(w in p['photo_alt'] for w in words)), None)


def visible_turns(turns, history, public, now):
    """Drop generated replies whose photo or connected source has been withdrawn."""
    plans = {t.reply_bubble.plan_id: t.reply_bubble.plan for t in turns if hasattr(t, 'reply_bubble')}
    sent = list(history.filter(attachment_version__isnull=False).filter(
        Q(pk__in=[t.pk for t in turns]) | Q(reply_bubble__plan_id__in=plans),
    ).values_list('pk', 'reply_bubble__plan_id', 'attachment_post_id', 'attachment_version'))
    versions = dict(photo_posts(public).filter(pk__in=[row[2] for row in sent]).values_list('pk', 'version'))
    withdrawn = [(pk, plan_id) for pk, plan_id, post_id, version in sent if versions.get(post_id) != version]
    hidden_ids = {pk for pk, _ in withdrawn}
    hidden_plans = {plan_id for _, plan_id in withdrawn if plan_id is not None}
    linked_plans = {pk: plan.context_versions for pk, plan in plans.items()
                    if POST_LINK_KEY in plan.context_versions}
    if linked_plans:
        post_ids = {pk for versions in linked_plans.values() for pk in versions[POST_LINK_KEY]}
        posts = {str(post.pk): post for post in public.filter(pk__in=post_ids).select_related('photo')}
        hidden_plans.update(pk for pk, versions in linked_plans.items()
                            if not post_link_is_current(versions, posts, now))
    return [turn for turn in turns if turn.pk not in hidden_ids and (
        not hasattr(turn, 'reply_bubble') or turn.reply_bubble.plan_id not in hidden_plans
    )]
