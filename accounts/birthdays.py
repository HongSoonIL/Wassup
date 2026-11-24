"""Calendar rules shared by profile recall and birthday delivery."""
import calendar
import re


def observed_days(day):
    dates = [(day.month, day.day)]
    if day.month == 2 and day.day == 28 and not calendar.isleap(day.year):
        dates.append((2, 29))
    return dates


def asks_birthday(text):
    return bool(re.fullmatch(
        r'\s*(?:(?:혹시|근데|너|넌)[,\s]+)*(?:내|나|제)\s*생일(?:이|은|을|날)?\s*(?:날짜(?:가|를)?\s*)?'
        r'(?:언제(?:인지(?:\s*(?:알아|기억나|말해\s*줘|알려\s*줘))?|였(?:더라|지)|더라|라고\s*했지|지|게|야|니)?|(?:몇\s*월\s*)?며칠(?:인지(?:\s*(?:알아|기억나))?|이야|이지|이더라)?|기억(?:나|해)|알아)'
        r'[요?？.!~ㅋㅎ\s]*', text))


def announces_birthday(text):
    return bool(re.fullmatch(
        r'\s*(?:(?:근데|참|있잖아)[,\s]+)?(?:(?:나|내|제)\s*오늘|오늘(?:이|은)?\s*(?:나|내|제))\s*'
        r'생일(?:이야|이다|이에요|이거든|인데|임)?[!~.ㅋㅎ\s]*', text))


def directed_birthday_wish(trigger, text):
    if '생일' not in text and '생축' not in text:
        return False
    other = r'(?:엄마|아빠|동생|언니|오빠|친구|선생님)(?:의|님|네)?\s*생일'
    return not (re.search(other, trigger + ' ' + text)
                or re.search(r'(?:너|네|니)\s*생일|문구|대신|써\s*줘|전해\s*줘', trigger))


def celebrates(text):
    return bool(re.search(r'축하(?:해(?:요)?|한다|행|하고|드려요|드립니다)(?=[^가-힣]|$)|생일\s*축하(?=[!~.🎂🥳🎉]|$)|생축(?=[^가-힣]|$)|해피\s*버스데이|happy birthday', text, re.I))
