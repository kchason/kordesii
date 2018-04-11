"""
DC3-Kordesii framework primary object used for execution of decoders and collection of metadata.
"""
import os
import sys
import traceback
import cStringIO
import contextlib
import glob
import json
import hashlib
import tempfile
import shutil
import base64

from kordesii import decoders
import kordesii.kordesiiidahelper as idahelper

# Constant fields
FIELD_DEBUG = "debug"
FIELD_STRINGS = "strings"
FIELD_FILES = "files"
FIELD_IDB = "idb"


class kordesiireporter(object):
    """
    Class for doing decoder execution and metadata reporting.

    This class contains state and data about the current string decoder run, including extracted metadata,
    holding the actual sample, etc.

    Re-using an instance of this class on multiple samples is possible and should be safe, but it is not
    recommended.

    Parameters:
        decoderdir: sets attribute
        tempdir: sets attribute
        disabledebug: disable inclusion of debug messages in output
        disabletempcleanup: disable cleanup (deletion) of temp files

    Attributes:
        decoderdir: Directory where decoders reside. This should not be
            changed except through constructor and decoders should have no reason to read this value.
        tempdir: directory where temporary files should be created. Files created in this directory should
            be deleted by decoder. See managed_tempdir for mwcp managed directory
        data: buffer containing input file to parsed
        handle: file handle (stringio) of file to parsed
        metadata: Dictionary containing the metadata extracted from the malware by the decoder
        errors: list of errors generated by framework. Generally decoders should not set these, they should
            use debug instead
        debug: list of debug messages generated by framework or decoders.
        strings: list of strings decoded by decoders.

    """

    # changing this is not recommended
    __decodernamepostfix = "_StringDecode"

    def __init__(self,
                 decoderdir=None,
                 tempdir=None,
                 disabletempcleanup=False,
                 disabledebug=False,
                 enableidalog=False,
                 base64outputfiles=False,
                 ):

        # defaults
        self.decoderdir = os.path.dirname(decoders.__file__)
        self.tempdir = tempfile.gettempdir()
        self.data = ''
        self.metadata = {}
        self.errors = []
        self.ida_log = ''

        self.__filename = ''
        self.__tempfilename = ''
        self.__managed_tempdir = ''

        if decoderdir:
            self.decoderdir = decoderdir
        if tempdir:
            self.tempdir = tempdir

        self.__disabletempcleanup = disabletempcleanup
        self.__disabledebug = disabledebug
        self.__enableidalog = enableidalog
        self.__base64outputfiles = base64outputfiles

    def filename(self):
        """
        Returns the filename of the input file. If input was not a filesystem object, we create a temp file that is cleaned up after decoder is finished (unless tempcleanup is disabled)
        """
        if self.__filename:
            # we were given a filename, give it back
            return self.__filename
        else:
            # we were passed data buffer. Lazy initialize a temp file for this
            if not self.__tempfilename:
                with tempfile.NamedTemporaryFile(delete=False, dir=self.managed_tempdir(),
                                                 prefix="kordesii-inputfile-") as tfile:
                    tfile.write(self.data)
                    self.__tempfilename = tfile.name

                if self.__disabletempcleanup:
                    self.debug("Using tempfile as input file: %s" % (self.__tempfilename))

            return self.__tempfilename

    def filedir(self):
        """
        Return the directory path of the file object, whether a filesystem object or a created temp file.
        """

        return os.path.dirname(os.path.abspath(self.filename()))

    def managed_tempdir(self):
        """
        Returns the filename of a managed temporary directory. This directory will be deleted when decoder is finished, unless tempcleanup is disabled.
        """

        if not self.__managed_tempdir:
            self.__managed_tempdir = tempfile.mkdtemp(dir=self.tempdir, prefix="kordesii-managed_tempdir-")

            if self.__disabletempcleanup:
                self.debug("Using managed temp dir: %s" % (self.__managed_tempdir))

        return self.__managed_tempdir

    def error(self, message):
        """
        Record an error message--typically only framework reports error and decoders report via debug
        """
        messageu = self.convert_to_unicode(message)

        self.errors.append(messageu)

    def get_errors(self):
        """
        Return list of errors.
        """
        return self.errors

    def debug(self, message):
        """
        Record a debug message
        """
        fieldu = self.convert_to_unicode(FIELD_DEBUG)
        messageu = self.convert_to_unicode(message)

        if not self.__disabledebug:
            if fieldu not in self.metadata:
                self.metadata[fieldu] = []

            self.metadata[fieldu].append(messageu)

    def get_debug(self):
        """
        Return list of debug statements. Returns empty list if none.
        """
        if FIELD_DEBUG in self.metadata:
            return self.metadata[FIELD_DEBUG]

        return []

    def add_string(self, string):
        """
        Record a decoded string
        """
        fieldu = self.convert_to_unicode(FIELD_STRINGS)
        stringu = self.convert_to_unicode(string)

        if fieldu not in self.metadata:
            self.metadata[fieldu] = []

        self.metadata[fieldu].append(stringu)

    def get_strings(self):
        """
        Get a list of any recorded strings.
        """
        if FIELD_STRINGS in self.metadata:
            return self.metadata[FIELD_STRINGS]

        return []

    def set_ida_log(self, log):
        """
        Record log contents produced by IDA.
        """
        logu = self.convert_to_unicode(log)

        if self.__enableidalog:
            self.ida_log = logu

    def get_ida_log(self):
        """
        Return ida log.
        """
        return self.ida_log

    def set_idb(self, idb_path):
        """
        Set contents for produced IDB/I64 file.
        """

        if not os.path.isfile(idb_path):
            return

        fieldu = self.convert_to_unicode(FIELD_IDB)
        filenameu = self.convert_to_unicode(os.path.basename(idb_path))
        self.metadata[fieldu] = {"name": filenameu}

        if self.__base64outputfiles:
            self.metadata[fieldu]["data"] = base64.b64encode(open(idb_path, 'rb').read())

    def add_output_file(self, filename, data, description=""):
        """
        Add file and its data to metadata.
        """
        fieldu = self.convert_to_unicode(FIELD_FILES)
        filenameu = self.convert_to_unicode(filename)
        descriptionu = self.convert_to_unicode(description)
        md5 = hashlib.md5(data).hexdigest()

        if fieldu not in self.metadata:
            self.metadata[fieldu] = []

        if self.__base64outputfiles:
            self.metadata[fieldu].append([filenameu, descriptionu, md5, base64.b64encode(data)])
        else:
            self.metadata[fieldu].append([filenameu, descriptionu, md5])

    def get_file_contents(self, filename):
        """
        If the file name exists and has its contents are stored in the reporter, then take
        the base64 encoded contents, base64 decode it, and return it.
        """
        if FIELD_FILES in self.metadata:
            for entry in self.metadata[FIELD_FILES]:
                if entry[0] == filename and len(entry) == 4:
                    return base64.b64decode(entry[3])

        return None

    def list_decoders(self):
        """
        Retrieve list of decoder
        """
        decoders = []

        # Look for .py files with the decoder name postfix
        decoder_file_postfix = self.__decodernamepostfix + '.py'
        for fullpath in glob.glob(os.path.join(self.decoderdir, '*' + decoder_file_postfix)):
            basefile = os.path.basename(fullpath)
            decoders.append(basefile[:-len(decoder_file_postfix)])

        return decoders

    def get_decoder_path(self, decoder_name):
        """
        Descripton:
            Given a decoder name, either full or just the family, get its path. First, try finding the Decoders
            directory that should be a sibling to kordesii's parent and look in there. If that fails, return None.

        Input:
            decoder_name - The name of the decoder (just the family name)

        Output:
            The full path of the decoder

        Raises:
            ValueError if the decoder could not be found.
        """

        decoder_file_name = decoder_name + self.__decodernamepostfix + '.py'
        decoder_path = os.path.join(self.decoderdir, decoder_file_name)
        if os.path.isfile(decoder_path):
            return decoder_path
        else:
            raise ValueError('Failed to find decoder: {}'.format(decoder_name))

    def run_decoder(self,
                    name,
                    filename=None,
                    data=None,
                    timeout=None,
                    autonomous=True,
                    cleanup_txt_files=True,
                    cleanup_output_files=False,
                    cleanup_idb_files=False,
                    ):
        """
        Runs specified decoder on file

        Args:
            name: name of decoder module to run
            filename: file to parse
            data: use data as file instead of loading data from filename
        """
        try:
            with self.__redirect_stdout():
                self.__reset()

                if filename:
                    self.__filename = filename
                else:
                    self.data = data

                # Run decoder
                decoder_path = self.get_decoder_path(name)
                file_path = self.filename()
                idahelper.run_ida(self,
                                  decoder_path,
                                  file_path,
                                  timeout=timeout,
                                  autonomous=autonomous,
                                  log=self.__enableidalog,
                                  cleanup_txt_files=cleanup_txt_files,
                                  cleanup_output_files=cleanup_output_files,
                                  cleanup_idb_files=cleanup_idb_files)

        except (Exception, SystemExit) as e:
            if filename:
                identifier = filename
            else:
                identifier = hashlib.md5(data).hexdigest()
            self.error("Error running decoder {} on {}: {}".format(name, identifier, traceback.format_exc()))

        finally:
            self.__cleanup()

    def pprint(self, data):
        """
        JSON Pretty Print data
        """
        return json.dumps(data, indent=4)

    def convert_to_unicode(self, input_string):
        if isinstance(input_string, unicode):
            return input_string
        else:
            return unicode(input_string, encoding='utf8', errors='replace')

    def output_text(self):
        """
        Output in human readable report format
        """

        print self.get_output_text().encode('utf-8')

    def get_output_text(self):
        """
        Get data in human readable report format.
        """

        output = u""

        output += u"----Decoded Strings----\n\n"

        if FIELD_STRINGS not in self.metadata:
            output += u"No decoded strings found\n"
        else:
            for item in self.metadata[FIELD_STRINGS]:
                output += u"{}\n".format(item)

        if FIELD_FILES in self.metadata:
            output += u"\n----Files----\n\n"
            for item in self.metadata[FIELD_FILES]:
                filename = item[0]
                output += u"{}\n".format(filename)

        if FIELD_IDB in self.metadata:
            output += u"\n----IDB----\n\n"
            output += u"{}\n".format(self.metadata[FIELD_IDB]["name"])

        if FIELD_DEBUG in self.metadata:
            output += u"\n----Debug----\n\n"
            for item in self.metadata[FIELD_DEBUG]:
                output += u"{}\n".format(item)

        if self.ida_log:
            output += u"\n----IDA Log----\n\n"
            output += u"{}\n".format(self.ida_log)

        if self.errors:
            output += u"\n----Errors----\n\n"
            for item in self.errors:
                output += u"{}\n".format(item)

        return output

    @contextlib.contextmanager
    def __redirect_stdout(self):
        """Redirects stdout temporarily while in a with statement."""
        debug_stdout = cStringIO.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = debug_stdout
        try:
            yield
        finally:
            if not self.__disabledebug:
                for line in debug_stdout.getvalue().splitlines():
                    self.debug(line)
            sys.stdout = orig_stdout

    def __reset(self):
        """
        Reset all the data in the reporter object that is set during the run_decoder function

        Goal is to make the reporter safe to use for multiple run_decoder instances
        """
        self.__filename = ''
        self.__tempfilename = ''
        self.__managed_tempdir = ''

        self.data = ''
        self.handle = None

        self.metadata = {}
        self.errors = []
        self.ida_log = ''

    def __cleanup(self):
        """
        Cleanup things
        """

        if not self.__disabletempcleanup:
            if self.__tempfilename:
                try:
                    os.remove(self.__tempfilename)
                except Exception as e:
                    self.debug("Failed to purge temp file: %s, %s" % (self.__tempfilename, str(e)))
                self.__tempfilename = ''

            if self.__managed_tempdir:
                try:
                    shutil.rmtree(self.__managed_tempdir, ignore_errors=True)
                except Exception as e:
                    self.debug("Failed to purge temp dir: %s, %s" % (self.__managed_tempdir, str(e)))
                self.__managed_tempdir = ''

        self.__tempfilename = ''
        self.__managed_tempdir = ''

    def __del__(self):
        self.__cleanup()
