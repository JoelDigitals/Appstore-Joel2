from django import template

register = template.Library()

@register.filter
def get_image_url(obj, field_name='image'):
    """
    Universal-Filter für Bild-URLs (ImgBB/extern priorisiert, dann lokal)
    
    Usage: 
    - {{ app|get_image_url:"icon" }} für App Icons
    - {{ screenshot|get_image_url:"image" }} für Screenshots
    - {{ developer|get_image_url:"logo" }} für Developer Logos
    
    Priorität:
    1. {field_name}_url (z.B. icon_url, image_url, logo_url)
    2. {field_name}.url (lokale Datei)
    3. None
    """
    if obj is None:
        return ''
    
    # Prüfe auf URL-Feld (ImgBB/extern)
    url_field = f'{field_name}_url'
    if hasattr(obj, url_field):
        url = getattr(obj, url_field)
        if url:
            return url
    
    # Prüfe auf Datei-Feld (lokal)
    file_field = getattr(obj, field_name, None)
    if file_field and hasattr(file_field, 'url'):
        return file_field.url
    
    return ''