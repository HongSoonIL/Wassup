"""Resolve personal post evidence through the quote actually sent to this room."""

import re

from django.db.models import Q

from accounts.models import PersonalPost
from llm import chat_photos


KEY = 'personal_post'
POST_REFERENCE = re.compile(r'(?:내가?|아까|방금|그|이)\s*(?:올린\s*)?(?:사진|게시물|피드|글)|올린\s*(?:사진|글)')
FRIEND_REFERENCE = re.compile(r'(?:네가|니가|너가|너의|네|니)\s*(?:올린|보낸|찍은|피드|게시물|사진)')
FOLLOWUP = re.compile(r'그거|그게|그건|거기|뭐가|어떤|무슨|어때|어땠|보였|보여|보이는|맘에|마음에|궁금|왜|어디|누구|몇\s*시|찍었')


def quoted_posts(room):
    return PersonalPost.objects.filter(owner_id=room.user_id, reaction_message__room_id=room.pk)


def visible_turns(turns, room):
    """Keep withdrawn source details out of future prompts while preserving displayed messages."""
    def source(turn):
        if turn.source_version is not None and not turn.source_post_id:
            return turn.source_personal_post_id or 0
        if hasattr(turn, 'reply_bubble'):
            return turn.reply_bubble.plan.context_versions.get(KEY)
        return None

    sources = {t.pk: source(t) for t in turns}
    ids = {pk for pk in sources.values() if pk is not None}
    if not ids:
        return turns
    current = set(quoted_posts(room).filter(pk__in=ids).values_list('pk', flat=True))
    return [turn for turn in turns if sources[turn.pk] is None or sources[turn.pk] in current]


def for_turn(history, turns, room):
    if not turns:
        return None
    trigger = turns[0]
    if trigger.source_post_id or trigger.kind == 'photo':
        return None
    # Only the latest actual quote or image can anchor an implicit reference.
    anchor = (history.filter(Q(source_version__isnull=False) | Q(kind='photo'),
                             created_at__gte=trigger.created_at - chat_photos.RECENT_QUOTE_WINDOW)
              .order_by('-pk').values('pk', 'source_personal_post_id').first())
    if not anchor or not anchor['source_personal_post_id']:
        return None
    post = (quoted_posts(room).filter(pk=anchor['source_personal_post_id'])
            .values('id', 'caption', 'reaction__photo_description').first())
    if not post:
        return None
    description = post.pop('reaction__photo_description') or ''
    question = trigger.text
    subject = chat_photos.post_topic_words(post['caption'] + ' ' + description)
    pointed = POST_REFERENCE.search(question)
    # An explicit new topic about the friend takes precedence.
    if FRIEND_REFERENCE.search(question) or not pointed and re.search(r'뭐\s*해|요즘\s*뭐', question):
        return None
    previous = next((turn for turn in turns[1:] if turn.sender == 'character'), None)
    just_quoted = previous is not None and previous.pk == anchor['pk']
    previous_used_post = (
        previous is not None and hasattr(previous, 'reply_bubble')
        and previous.reply_bubble.plan.context_versions.get(KEY) == post['id']
    )
    related = bool(chat_photos.post_topic_words(question) & subject)
    if not (pointed or related or just_quoted or previous_used_post and FOLLOWUP.search(question)):
        return None
    return {**post, 'photo_description': description, 'quote_message_id': anchor['pk']}


INSTRUCTIONS = """personal_post는 이 방에서 네가 인용한 상대의 게시물이다. caption은 상대가 쓴 글이고 photo_description은 실제 사진에서 보인 묘사다.
사용자가 그 글에 관해 대화를 이으면 두 자료를 구분해 답한다. 상대의 일을 네 경험으로 말하거나, 글 속 내용을 사진에 보인다고 하지 않는다.
사람의 신원, 관계, 감정, 이유, 장소나 시각은 명시된 정보만 쓴다. 모르는 내용은 추측하지 말고 짧게 되묻는다. 사용자가 화제를 바꾸면 그 말에 답한다."""


def sample(post, question):
    if re.search(r'왜|이유|누구|어디|언제|몇\s*시', question):
        return ['그건 나도 궁금해']
    if post['photo_description']:
        return [post['photo_description']]
    if post['caption']:
        return [f"네가 쓴 ‘{post['caption'][:160]}’ 얘기였어"]
    return ['어떤 걸 말하는 거야?']
