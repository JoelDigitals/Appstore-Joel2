from django import forms
from .models import App, Version, Developer, AppScreenshot, ImgBBUploader
from django.conf import settings

class AppWithVersionForm(forms.ModelForm):
    version_number = forms.CharField(max_length=50, label="Versionsnummer")
    file = forms.FileField(label="App-Datei")
    release_notes = forms.CharField(widget=forms.Textarea, required=False, label="Release Notes")
    scheduled_release_at = forms.DateTimeField(
        required=False,
        label="Geplantes Release-Datum",
        help_text="Optional: Zeitpunkt der Veröffentlichung nach bestandener Prüfung (leer = sofort)",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    # ImgBB Upload Option
    use_imgbb = forms.BooleanField(
        required=False,
        initial=True,
        label="Bild zu ImgBB hochladen",
        help_text="Wenn aktiviert, wird das Icon zu ImgBB hochgeladen (schnellerer Zugriff). Bei Fehler wird lokal gespeichert."
    )

    class Meta:
        model = App
        fields = ['name', 'description', 'language', 'platform', 'age_rating',
                  'category', 'subcategory', 'icon']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['icon'].required = False
        self.fields['use_imgbb'].widget = forms.CheckboxInput(attrs={
            'class': 'w-5 h-5 rounded border-gray-600 text-primary bg-slate-700 focus:ring-primary'
        })
        input_css = ('w-full bg-slate-800/50 border border-gray-700 rounded-xl px-4 py-3 '
                     'text-gray-100 focus:outline-none focus:border-primary focus:ring-1 '
                     'focus:ring-primary transition-all')
        for field_name in ('description', 'language', 'age_rating', 'category',
                           'subcategory', 'release_notes', 'scheduled_release_at'):
            self.fields[field_name].widget.attrs['class'] = input_css
        self.fields['description'].widget.attrs['rows'] = 4

    def clean(self):
        cleaned_data = super().clean()
        icon = cleaned_data.get('icon')

        if not icon:
            raise forms.ValidationError("Bitte lade ein Icon hoch.")

        scheduled = cleaned_data.get('scheduled_release_at')
        if scheduled:
            from django.utils import timezone as tz
            if scheduled < tz.now():
                self.add_error('scheduled_release_at', 'Das Release-Datum muss in der Zukunft liegen.')

        return cleaned_data

    def save(self, developer, commit=True):
        app = super().save(commit=False)
        app.developer = developer

        icon = self.cleaned_data.get('icon')
        use_imgbb = self.cleaned_data.get('use_imgbb', True)

        # Versuche ImgBB Upload wenn aktiviert und API Key vorhanden
        if use_imgbb and settings.IMGBB_API_KEY and icon:
            result = ImgBBUploader.upload_image(icon)
            if result['success']:
                app.icon_url = result['url']
                app.icon = None  # Nicht lokal speichern
            else:
                # Fallback zu lokalem Speichern
                app.icon = icon
                app.icon_url = ''
        elif icon:
            # Lokales Speichern
            app.icon = icon
            app.icon_url = ''

        if commit:
            app.save()

        return app

    def save_version(self, app):
        """Erstellt die erste Version der App (gleiche Pipeline wie spätere Uploads: Prüfung + JDS Cloud)."""
        uploaded_file = self.cleaned_data['file']
        version = Version.objects.create(
            app=app,
            version_number=self.cleaned_data['version_number'],
            file=uploaded_file,
            original_filename=uploaded_file.name,
            release_notes=self.cleaned_data['release_notes'],
            scheduled_release_at=self.cleaned_data.get('scheduled_release_at'),
            checking_status='pending',
            approved=False,
        )
        return version


class AppEditForm(forms.ModelForm):
    use_imgbb = forms.BooleanField(
        required=False, 
        initial=True,
        label="Neues Bild zu ImgBB hochladen (falls geändert)",
    )
    
    class Meta:
        model = App
        fields = ['name', 'description', 'language', 'platform', 'age_rating',
                  'category', 'subcategory', 'icon']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['icon'].required = False
    
    def save(self, commit=True):
        app = super().save(commit=False)
        
        icon = self.cleaned_data.get('icon')
        use_imgbb = self.cleaned_data.get('use_imgbb', True)
        
        # Nur wenn neues Icon hochgeladen wurde
        if icon:
            if use_imgbb and settings.IMGBB_API_KEY:
                result = ImgBBUploader.upload_image(icon)
                if result['success']:
                    # Lösche altes lokales Icon wenn vorhanden
                    if app.icon:
                        app.icon.delete(save=False)
                    app.icon_url = result['url']
                    app.icon = None
                else:
                    app.icon = icon
                    app.icon_url = ''
            else:
                app.icon = icon
                app.icon_url = ''
        
        if commit:
            app.save()
        return app


class ScreenshotForm(forms.ModelForm):
    use_imgbb = forms.BooleanField(
        required=False, 
        initial=True,
        label="Zu ImgBB hochladen",
    )
    
    class Meta:
        model = AppScreenshot
        fields = ['image']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['image'].required = False
    
    def clean(self):
        cleaned_data = super().clean()
        image = cleaned_data.get('image')
        
        if not image:
            raise forms.ValidationError("Bitte lade ein Bild hoch.")
        
        return cleaned_data
    
    def save(self, app=None, commit=True):
        screenshot = super().save(commit=False)
        
        if app:
            screenshot.app = app
        
        image = self.cleaned_data.get('image')
        use_imgbb = self.cleaned_data.get('use_imgbb', True)
        
        if image:
            if use_imgbb and settings.IMGBB_API_KEY:
                result = ImgBBUploader.upload_image(image)
                if result['success']:
                    screenshot.image_url = result['url']
                    screenshot.image = None
                else:
                    screenshot.image = image
                    screenshot.image_url = ''
            else:
                screenshot.image = image
                screenshot.image_url = ''
        
        if commit:
            screenshot.save()
        return screenshot


class DeveloperForm(forms.ModelForm):
    use_imgbb = forms.BooleanField(
        required=False, 
        initial=True,
        label="Logo zu ImgBB hochladen",
    )
    
    class Meta:
        model = Developer
        fields = ['name', 'description', 'website', 'email', 'logo', 
                  'youtube', 'twitter', 'github']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['logo'].required = False
    
    def save(self, commit=True):
        developer = super().save(commit=False)
        
        logo = self.cleaned_data.get('logo')
        use_imgbb = self.cleaned_data.get('use_imgbb', True)
        
        if logo:
            if use_imgbb and settings.IMGBB_API_KEY:
                result = ImgBBUploader.upload_image(logo)
                if result['success']:
                    # Lösche altes lokales Logo wenn vorhanden
                    if developer.logo:
                        developer.logo.delete(save=False)
                    developer.logo_url = result['url']
                    developer.logo = None
                else:
                    developer.logo = logo
                    developer.logo_url = ''
            else:
                developer.logo = logo
                developer.logo_url = ''
        
        if commit:
            developer.save()
        return developer


class VersionForm(forms.ModelForm):
    release_tag_custom = forms.CharField(
        max_length=50, required=False,
        label="Benutzerdefinierter Tag",
        help_text="Nur ausfüllen wenn 'Benutzerdefiniert' gewählt wurde (z. B. v2.0-rc3)",
        widget=forms.TextInput(attrs={'placeholder': 'z. B. v2.0-rc3'}),
    )

    scheduled_release_at = forms.DateTimeField(
        required=False,
        label="Geplantes Release-Datum",
        help_text="Optional: Zeitpunkt der Veröffentlichung nach bestandener Prüfung (leer = sofort)",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    class Meta:
        model = Version
        fields = ['version_number', 'file', 'release_notes', 'release_tag',
                  'release_tag_custom', 'scheduled_release_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['release_tag'].initial = 'stable'
        self.fields['release_tag'].help_text = "Wähle den Release-Kanal für diese Version"

    def clean(self):
        cleaned = super().clean()
        tag = cleaned.get('release_tag')
        custom = cleaned.get('release_tag_custom', '').strip()
        if tag == 'custom' and not custom:
            self.add_error('release_tag_custom', 'Bitte gib einen benutzerdefinierten Tag ein.')
        scheduled = cleaned.get('scheduled_release_at')
        if scheduled:
            from django.utils import timezone as tz
            if scheduled < tz.now():
                self.add_error('scheduled_release_at', 'Das Release-Datum muss in der Zukunft liegen.')
        return cleaned

from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm

class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'placeholder': 'E-Mail-Adresse'})
    )

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')
        widgets = {
            'username': forms.TextInput(attrs={'placeholder': 'Benutzername'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].widget.attrs['placeholder'] = 'Passwort'
        self.fields['password2'].widget.attrs['placeholder'] = 'Passwort bestätigen'
    
