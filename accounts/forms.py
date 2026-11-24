from zoneinfo import ZoneInfo

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone

from .models import User


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ('nickname', 'bio')

    def clean_bio(self):
        value = self.cleaned_data['bio']
        if len(value.splitlines()) > 1:
            raise forms.ValidationError('한 줄로 적어 주세요.')
        return value


class AgreementCheckbox(forms.CheckboxInput):
    def value_from_datadict(self, data, files, name):
        # CheckboxInput treats every nonempty string except "false" as checked.
        return str(data.get(name, '')).lower() in {'on', 'true', '1', 'yes'}


class SignupForm(UserCreationForm):
    birth_date = forms.DateField(
        label='생년월일',
        input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
    )
    agree = forms.BooleanField(label='AI 친구 안내 및 대화 저장에 동의합니다.', widget=AgreementCheckbox)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'password1', 'password2', 'birth_date', 'agree')

    def clean_birth_date(self):
        born = self.cleaned_data['birth_date']
        today = timezone.localdate(timezone=ZoneInfo('Asia/Seoul'))
        birthday_is_upcoming = (today.month, today.day) < (born.month, born.day)
        if today.year - born.year - birthday_is_upcoming < 18:
            raise forms.ValidationError('만 18세 이상부터 가입할 수 있어요.')
        return born

    def save(self, commit=True):
        self.instance.birth_year = self.cleaned_data['birth_date'].year
        self.instance.agreed_at = timezone.now()
        return super().save(commit=commit)
