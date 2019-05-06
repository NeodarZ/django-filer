# -*- coding: utf-8 -*-
from __future__ import absolute_import

import logging
import os

from django.db import models
from django.utils import six
from django.utils.translation import ugettext_lazy as _

from .. import settings as filer_settings
from ..utils.compatibility import GTE_DJANGO_1_10, PILImage, is_authenticated
from ..utils.filer_easy_thumbnails import FilerThumbnailer
from ..utils.pil_exif import get_exif_for_file
from .filemodels import File

from django.conf import settings

from easy_thumbnails import namers
from easy_thumbnails.conf import settings as esettings

logger = logging.getLogger(__name__)


class BaseImage(File):
    SIDEBAR_IMAGE_WIDTH = 210
    DEFAULT_THUMBNAILS = {
        'admin_clipboard_icon': {'size': (32, 32), 'crop': True,
                                 'upscale': True},
        'admin_sidebar_preview': {'size': (SIDEBAR_IMAGE_WIDTH, 0), 'upscale': True},
        'admin_directory_listing_icon': {'size': (48, 48),
                                         'crop': True, 'upscale': True},
        'admin_tiny_icon': {'size': (32, 32), 'crop': True, 'upscale': True},
    }
    file_type = 'Image'
    _icon = "image"

    _height = models.IntegerField(null=True, blank=True)
    _width = models.IntegerField(null=True, blank=True)

    default_alt_text = models.CharField(_('default alt text'), max_length=255, blank=True, null=True)
    default_caption = models.CharField(_('default caption'), max_length=255, blank=True, null=True)

    subject_location = models.CharField(_('subject location'), max_length=64, blank=True,
                                        default='')
    file_ptr = models.OneToOneField(
        to='filer.File', parent_link=True,
        related_name='%(app_label)s_%(class)s_file',
        on_delete=models.CASCADE,
    )

    @classmethod
    def matches_file_type(cls, iname, ifile, request):
        # This was originally in admin/clipboardadmin.py  it was inside of a try
        # except, I have moved it here outside of a try except because I can't
        # figure out just what kind of exception this could generate... all it was
        # doing for me was obscuring errors...
        # --Dave Butler <croepha@gmail.com>
        iext = os.path.splitext(iname)[1].lower()
        return iext in ['.jpg', '.jpeg', '.png', '.gif']

    def file_data_changed(self, post_init=False):
        attrs_updated = super(BaseImage, self).file_data_changed(post_init=post_init)
        if attrs_updated:
            try:
                try:
                    imgfile = self.file.file
                except ValueError:
                    imgfile = self.file_ptr.file
                imgfile.seek(0)
                self._width, self._height = PILImage.open(imgfile).size
                imgfile.seek(0)
            except Exception:
                if post_init is False:
                    # in case `imgfile` could not be found, unset dimensions
                    # but only if not initialized by loading a fixture file
                    self._width, self._height = None, None
        return attrs_updated

    def save(self, *args, **kwargs):
        self.has_all_mandatory_data = self._check_validity()
        super(BaseImage, self).save(*args, **kwargs)

    def _check_validity(self):
        if not self.name:
            return False
        return True

    def sidebar_image_ratio(self):
        if self.width:
            return float(self.width) / float(self.SIDEBAR_IMAGE_WIDTH)
        else:
            return 1.0

    def _get_exif(self):
        if hasattr(self, '_exif_cache'):
            return self._exif_cache
        else:
            if self.file:
                self._exif_cache = get_exif_for_file(self.file)
            else:
                self._exif_cache = {}
        return self._exif_cache
    exif = property(_get_exif)

    def has_edit_permission(self, request):
        return self.has_generic_permission(request, 'edit')

    def has_read_permission(self, request):
        return self.has_generic_permission(request, 'read')

    def has_add_children_permission(self, request):
        return self.has_generic_permission(request, 'add_children')

    def has_generic_permission(self, request, permission_type):
        """
        Return true if the current user has permission on this
        image. Return the string 'ALL' if the user has all rights.
        """
        user = request.user
        if not is_authenticated(user):
            return False
        elif user.is_superuser:
            return True
        elif user == self.owner:
            return True
        elif self.folder:
            return self.folder.has_generic_permission(request, permission_type)
        else:
            return False

    @property
    def label(self):
        if self.name in ['', None]:
            return self.original_filename or 'unnamed file'
        else:
            return self.name

    @property
    def width(self):
        return self._width or 0

    @property
    def height(self):
        return self._height or 0

    def _generate_thumbnails(self, required_thumbnails):
        _thumbnails = {}
        for name, opts in six.iteritems(required_thumbnails):
            try:
                opts.update({'subject_location': self.subject_location})
                # This is a ugly because for don't generate a thumbnail
                # each time someone view the thumbnail
                try:
                    thumbnail_options = self.file.get_options(opts)
                    path, source_filename = os.path.split(self.file.name)
                    thumbnail_extension = os.path.splitext(source_filename)[1][1:]
                    if thumbnail_extension == 'jpeg':
                        thumbnail_extension = 'jpg'
                    elif thumbnail_extension == "gif":
                        thumbnail_extension = 'png'
                    prepared_options = []
                    prepared_options.append('%sx%s' % tuple(opts['size']))

                    prepared_options.append('q%s' % (esettings.THUMBNAIL_QUALITY))

                    for key, value in sorted(six.iteritems(opts)):
                        if key == key.upper():
                            # Uppercase options aren't used by prepared options (a primary
                            # use of prepared options is to generate the filename -- these
                            # options don't alter the filename).
                            continue
                        if not value or key in ('size', 'quality'):
                            continue
                        if value is True:
                            prepared_options.append(key)
                            continue
                        if not isinstance(value, six.string_types):
                            try:
                                value = ','.join([six.text_type(item) for item in value])
                            except TypeError:
                                value = six.text_type(value)
                        prepared_options.append('%s-%s' % (key, value))

                    if 'subsampling-2' not in prepared_options:
                        if 'crop' in prepared_options:
                            for i in range(len(prepared_options)):
                                if prepared_options[i] == 'crop':
                                    prepared_options.insert(i+1, 'subsampling-2')
                        else:
                            for i in range(len(prepared_options)):
                                if prepared_options[i] == 'upscale':
                                    prepared_options.insert(i, 'subsampling-2')



                    filename_parts = [source_filename]
                    if ('%(opts)s' in self.file.thumbnail_basedir or
                        '%(opts)s' in self.file.thumbnail_subdir):
                        if thumbnail_extension != os.path.splitext(source_filename)[1][1:]:
                            filename_parts.append(thumbnail_extension)
                    else:
                        filename_parts += ['_'.join(prepared_options), thumbnail_extension]
                    filename = filename_parts[0]+"__"+filename_parts[1]+"."+filename_parts[2]
                    THUMBNAILS_BASE_DIR = filer_settings.FILER_STORAGES['public']['thumbnails']['THUMBNAIL_OPTIONS'].get('base_dir')
                    MEDIA_SIMPLE_PATH = os.path.join(path, filename)
                    MEDIA_PATH = os.path.join(THUMBNAILS_BASE_DIR, MEDIA_SIMPLE_PATH)
                    MEDIA_PATH_ABS = os.path.join(settings.MEDIA_ROOT, MEDIA_PATH)
                    MEDIA_PATH_REL = os.path.join(settings.MEDIA_URL, MEDIA_PATH)
                    if not os.path.isfile(MEDIA_PATH_ABS):
                        print("Generating: " + MEDIA_PATH_ABS)
                        thumb = self.file.get_thumbnail(opts)
                        _thumbnails[name] = thumb.url
                    else:
                        _thumbnails[name] = MEDIA_PATH_REL
                except Exception as e:
                    print(e)
            except Exception as e:
                # catch exception and manage it. We can re-raise it for debugging
                # purposes and/or just logging it, provided user configured
                # proper logging configuration
                if filer_settings.FILER_ENABLE_LOGGING:
                    logger.error('Error while generating thumbnail: %s', e)
                if filer_settings.FILER_DEBUG:
                    raise
        return _thumbnails

    @property
    def icons(self):
        required_thumbnails = dict(
            (size, {'size': (int(size), int(size)),
                    'crop': True,
                    'upscale': True,
                    'subject_location': self.subject_location})
            for size in filer_settings.FILER_ADMIN_ICON_SIZES)
        return self._generate_thumbnails(required_thumbnails)

    @property
    def icons_images(self):
        required_thumbnails = dict(
            (size, {'size': (int(size), int(size)),
                    'crop': True,
                    'upscale': True,
                    'subject_location': self.subject_location})
            for size in filer_settings.FILER_ADMIN_ICON_IMAGES_SIZES)
        return self._generate_thumbnails(required_thumbnails).get(filer_settings.FILER_ADMIN_ICON_IMAGES_SIZES[0])

    @property
    def thumbnails(self):
        return self._generate_thumbnails(BaseImage.DEFAULT_THUMBNAILS)

    @property
    def easy_thumbnails_thumbnailer(self):
        tn = FilerThumbnailer(
            file=self.file, name=self.file.name,
            source_storage=self.file.source_storage,
            thumbnail_storage=self.file.thumbnail_storage,
            thumbnail_basedir=self.file.thumbnail_basedir)
        return tn

    class Meta(object):
        app_label = 'filer'
        verbose_name = _('image')
        verbose_name_plural = _('images')
        abstract = True
        if GTE_DJANGO_1_10:
            default_manager_name = 'objects'
