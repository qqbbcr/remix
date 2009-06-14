"""
The main `Echo Nest`_ `Remix API`_ module for manipulating audio files and 
their associated `Echo Nest`_ `Analyze API`_ analyses.

AudioData, and getpieces by Robert Ochshorn
on 2008-06-06.  Some refactoring and everything else by Joshua Lifton
2008-09-07.  Refactoring by Ben Lacker 2009-02-11. Other contributions
by Adam Lindsay.

:group Base Classes: AudioAnalysis, AudioData
:group Audio-plus-Analysis Classes: AudioFile, LocalAudioFile, ExistingTrack, LocalAnalysis
:group Building Blocks: AudioQuantum, AudioSegment, AudioQuantumList
:group Exception Classes: FileTypeError

:group Audio helper functions: getpieces, mix
:group Utility functions: chain_from_mixed
:group Parsers: globalParserFloat, globalParserInt, *Parser

.. _Analyze API: http://developer.echonest.com/pages/overview?version=2
.. _Remix API: http://code.google.com/p/echo-nest-remix/
.. _Echo Nest: http://the.echonest.com/
"""

__version__ = "$Revision: 0 $"
# $Source$

import hashlib
import numpy
import os
import sys
import StringIO
import struct
import subprocess
import tempfile
import wave

import echonest.selection as selection
import echonest.web.analyze as analyze;
import echonest.web.util as util
import echonest.web.config as config


class AudioAnalysis(object) :
    """
    This class wraps `echonest.web` to allow transparent caching of the
    audio analysis of an audio file.
    
    For example, the following script will display the bars of a track
    twice::
    
        from echonest import *
        a = audio.AudioAnalysis('YOUR_TRACK_ID_HERE')
        a.bars
        a.bars
    
    The first time `a.bars` is called, a network request is made of the
    `Echo Nest`_ `Analyze API`_.  The second time time `a.bars` is called, the
    cached value is returned immediately.
    
    An `AudioAnalysis` object can be created using an existing ID, as in
    the example above, or by specifying the audio file to upload in
    order to create the ID, as in::
    
        a = audio.AudioAnalysis(filename='FULL_PATH_TO_AUDIO_FILE')
    
    .. _Analyze API: http://developer.echonest.com/pages/overview?version=2
    .. _Echo Nest: http://the.echonest.com/
    """
    
    #: Any variable in this listing is fetched over the network once
    #: and then cached.  Calling refreshCachedVariables will force a
    #: refresh.
    CACHED_VARIABLES = ( 'bars', 
                         'beats', 
                         'duration', 
                         'end_of_fade_in', 
                         'key',
                         'loudness',
                         'metadata',
                         'mode',
                         'sections',
                         'segments',
                         'start_of_fade_out',
                         'tatums',
                         'tempo',
                         'time_signature' )
    
    def __init__( self, audio, parsers=None ) :
        """
        Constructor.  If the argument is a valid local path or a URL,
        the track ID is generated by uploading the file to the `Echo Nest`_ 
        `Analyze API`_\.  Otherwise, the argument is assumed to be
        the track ID.
        
        :param audio: A string representing either a path to a local 
            file, a valid URL, or the ID of a file that has already 
            been uploaded for analysis.
        
        :param parsers: A dictionary of keys consisting of cached 
            variable names and values consisting of functions to 
            be used to parse those variables as they are cached.  
            No parsing is done for variables without parsing functions 
            or if the parsers argument is None.
        
        .. _Analyze API: http://developer.echonest.com/pages/overview?version=2
        .. _Echo Nest: http://the.echonest.com/        
        """
        
        if parsers is None :
            parsers = {}
        self.parsers = parsers
        
        if type(audio) is not str :
            # Argument is invalid.
            raise TypeError("Argument 'audio' must be a string representing either a filename, track ID, or MD5.")
        elif os.path.isfile(audio) or '.' in audio :
            # Argument is either a filename or URL.
            doc = analyze.upload(audio)
            self.id = doc.getElementsByTagName('thingID')[0].firstChild.data
        else:
            # Argument is a md5 or track ID.
            self.id = audio
            
        # Initialize cached variables to None.
        for cachedVar in AudioAnalysis.CACHED_VARIABLES : 
            self.__setattr__(cachedVar, None)
    
    def refreshCachedVariables( self ) :
        """
        Forces all cached variables to be updated over the network.
        """
        for cachedVar in AudioAnalysis.CACHED_VARIABLES : 
            self.__setattr__(cachedVar, None)
            self.__getattribute__(cachedVar)
    
    def __getattribute__( self, name ) :
        """
        This function has been modified to support caching of
        variables retrieved over the network. As a result, each 
        of the `CACHED_VARIABLES` is available as an accessor.
        """
        if name in AudioAnalysis.CACHED_VARIABLES :
            if object.__getattribute__(self, name) is None :
                getter = analyze.__dict__[ 'get_' + name ]
                value = getter(object.__getattribute__(self, 'id'))
                parseFunction = object.__getattribute__(self, 'parsers').get(name)
                if parseFunction :
                    value = parseFunction(value)
                self.__setattr__(name, value)
                if type(object.__getattribute__(self, name)) == AudioQuantumList:
                    object.__getattribute__(self, name).attach(self)
        return object.__getattribute__(self, name)
    
    def __setstate__(self, state):
        """
        Recreates circular references after unpickling.
        """
        self.__dict__.update(state)
        for cached_var in AudioAnalysis.CACHED_VARIABLES:
            if type(object.__getattribute__(self, cached_var)) == AudioQuantumList:
                object.__getattribute__(self, cached_var).attach(self)
    


class AudioData(object):
    """
    Handles audio data transparently. A smart audio container
    with accessors that include:
        
    sampleRate
        samples per second
    numChannels
        number of channels
    data
        a `numpy.array`_ 
        
    .. _numpy.array: http://docs.scipy.org/doc/numpy/reference/generated/numpy.array.html
    """
    def __init__(self, filename=None, ndarray = None, shape=None, sampleRate=None, numChannels=None):
        """
        Given an input `ndarray`, import the sample values and shape 
        (if none is specified) of the input `numpy.array`.
        
        Given a `filename` (and no input ndarray), use ffmpeg to convert
        the file to wave, then load the file into the data, 
        auto-detecting the sample rate, and number of channels.
        
        :param filename: a path to an audio file for loading its sample 
            data into the AudioData.data
        :param ndarray: a `numpy.array`_ instance with sample data
        :param shape: a tuple of array dimensions
        :param sampleRate: sample rate, in Hz
        :param numChannels: number of channels
        
        .. _numpy.array: http://docs.scipy.org/doc/numpy/reference/generated/numpy.array.html
        """
        if (filename is not None) and (ndarray is None) :
            if sampleRate is None or numChannels is None:
                # force sampleRate and numChannels to 44100 hz, 2
                sampleRate, numChannels = 44100, 2
                foo, fileToRead = tempfile.mkstemp(".wav")
                ffmpeg(filename, fileToRead, overwrite=True, numChannels=numChannels, sampleRate=sampleRate)
                parsestring = ffmpeg(fileToRead, overwrite=False)
                sampleRate, numChannels = settings_from_ffmpeg(parsestring[1])
            else:
                fileToRead = filename
            w = wave.open(fileToRead, 'r')
            numFrames = w.getnframes()
            raw = w.readframes(numFrames)
            sampleSize = numFrames * numChannels
            data = numpy.array(map(int,struct.unpack("%sh" % sampleSize, raw)), numpy.int16)
            ndarray = numpy.array(data, dtype=numpy.int16)
            if numChannels == 2:
                ndarray = numpy.reshape(ndarray, (numFrames, 2))    
        self.filename = filename
        self.sampleRate = sampleRate
        self.numChannels = numChannels
        
        if shape is None and isinstance(ndarray, numpy.ndarray):
            self.data = numpy.zeros(ndarray.shape, dtype=numpy.int16)
        elif shape is not None:
            self.data = numpy.zeros(shape, dtype=numpy.int16)
        else:
            self.data = None
        self.endindex = 0
        if ndarray is not None:
            self.endindex = len(ndarray)
            self.data[0:self.endindex] = ndarray
    
    def __getitem__(self, index):
        """
        Fetches a frame or slice. Returns an individual frame (if the index 
        is a time offset float or an integer sample number) or a slice if 
        the index is an `AudioQuantum` (or quacks like one).
        """
        if isinstance(index, float):
            index = int(index*self.sampleRate)
        elif hasattr(index, "start") and hasattr(index, "duration"):
            index =  slice(index.start, index.start+index.duration)
        
        if isinstance(index, slice):
            if ( hasattr(index.start, "start") and 
                 hasattr(index.stop, "duration") and 
                 hasattr(index.stop, "start") ) :
                index = slice(index.start.start, index.stop.start+index.stop.duration)
        
        if isinstance(index, slice):
            return self.getslice(index)
        else:
            return self.getsample(index)
    
    def getslice(self, index):
        "Help `__getitem__` return a new AudioData for a given slice"
        if isinstance(index.start, float):
            index = slice(int(index.start*self.sampleRate), int(index.stop*self.sampleRate), index.step)
        return AudioData(None, self.data[index],sampleRate=self.sampleRate)
    
    def getsample(self, index):
        """
        Help `__getitem__` return a frame (all channels for a given 
        sample index)
        """
        if isinstance(index, int):
            return self.data[index]
        else:
            #let the numpy array interface be clever
            return AudioData(None, self.data[index])
    
    def __add__(self, as2):
        """
        Returns a new `AudioData` from the concatenation of the two arguments.
        """
        if self.data is None:
            return AudioData(None, as2.data.copy())
        elif as2.data is None:
            return AudioData(None, self.data.copy())
        else:
            return AudioData(None, numpy.concatenate((self.data,as2.data)))
    
    def append(self, as2):
        "Appends the input to the end of this `AudioData`."
        self.data[self.endindex:self.endindex+len(as2)] = as2.data[0:]
        self.endindex += len(as2)
    
    def __len__(self):
        if self.data is not None:
            return len(self.data)
        else:
            return 0

    def encode(self, filename=None, mp3=None):
        """
        Outputs an MP3 or WAVE file to `filename`.
        Format is determined by `mp3` parameter.
        """
        if not mp3 and filename.lower().endswith('.wav'):
            mp3 = False
        else:
            mp3 = True
        if mp3:
            foo, tempfilename = tempfile.mkstemp(".wav")        
        else:
            tempfilename = filename
        fid = open(tempfilename, 'wb')
        # Based on Scipy svn
        # http://projects.scipy.org/pipermail/scipy-svn/2007-August/001189.html
        fid.write('RIFF')
        fid.write(struct.pack('i',0)) # write a 0 for length now, we'll go back and add it later
        fid.write('WAVE')
        # fmt chunk
        fid.write('fmt ')
        if self.data.ndim == 1:
            noc = 1
        else:
            noc = self.data.shape[1]
        bits = self.data.dtype.itemsize * 8
        sbytes = self.sampleRate*(bits / 8)*noc
        ba = noc * (bits / 8)
        fid.write(struct.pack('ihHiiHH', 16, 1, noc, self.sampleRate, sbytes, ba, bits))
        # data chunk
        fid.write('data')
        fid.write(struct.pack('i', self.data.nbytes))
        self.data.tofile(fid)
        # Determine file size and place it in correct
        # position at start of the file. 
        size = fid.tell()
        fid.seek(4)
        fid.write(struct.pack('i', size-8))
        fid.close()
        if not mp3:
            return tempfilename
        # now convert it to mp3
        if not filename.lower().endswith('.mp3'):
            filename = filename + '.mp3'
        try:
            bitRate = config.MP3_BITRATE
        except NameError:
            bitRate = 128
        ffmpeg(tempfilename, filename, bitRate=bitRate)

def ffmpeg(infile, outfile=None, overwrite=True, bitRate=None, numChannels=None, sampleRate=None):
    """
    Executes ffmpeg through the shell to convert or read media files.
    """
    command = "ffmpeg"
    if overwrite:
        command += " -y"
    command += " -i \"" + infile + "\""
    if bitRate is not None:
        command += " -ab " + str(bitRate) + "k"
    if numChannels is not None:
        command += " -ac " + str(numChannels)
    if sampleRate is not None:
        command += " -ar " + str(sampleRate)
    if outfile is not None:
        command += " " + outfile
    p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.communicate()

def settings_from_ffmpeg(parsestring):
    """
    Parses the output of ffmpeg to determine sample rate and frequency of 
    an audio file.
    """
    parse = parsestring.split('\n')
    freq, chans = 44100, 2
    for line in parse:
        if "Stream #0" in line and "Audio" in line:
            segs = line.split(", ")
            for s in segs:
                if "Hz" in s:
                    #print "Found: "+str(s.split(" ")[0])+"Hz"
                    freq = int(s.split(" ")[0])
                elif "stereo" in s:
                    #print "stereo"
                    chans = 2
                elif "mono" in s:
                    #print "mono"
                    chans = 1
    return freq, chans

def getpieces(audioData, segs):
    """
    Collects audio samples for output.
    Returns a new `AudioData` where the new sample data is assembled
    from the input audioData according to the time offsets in each 
    of the elements of the input segs (commonly an `AudioQuantumList`).
    
    :param audioData: an `AudioData` object
    :param segs: an iterable containing objects that may be accessed
        as slices or indices for an `AudioData`
    """
    #calculate length of new segment
    dur = 0
    for s in segs:
        dur += int(s.duration*audioData.sampleRate)
    
    dur += 100000 #another two seconds just for goodwill...
    
    #determine shape of new array
    if len(audioData.data.shape) > 1:
        newshape = (dur, audioData.data.shape[1])
        newchans = audioData.data.shape[1]
    else:
        newshape = (dur,)
        newchans = 1
    
    #make accumulator segment
    newAD = AudioData(shape=newshape,sampleRate=audioData.sampleRate, numChannels=newchans)
    
    #concatenate segs to the new segment
    for s in segs:
        newAD.append(audioData[s])
    
    return newAD

def mix(dataA,dataB,mix=0.5):
    """
    Mixes two `AudioData` objects. Assumes they have the same sample rate
    and number of channels.
    
    Mix takes a float 0-1 and determines the relative mix of two audios.
    i.e., mix=0.9 yields greater presence of dataA in the final mix.
    """
    if dataA.endindex > dataB.endindex:
        newdata = AudioData(ndarray=dataA.data)
        newdata.data *= float(mix)
        newdata.data[:dataB.endindex] += dataB.data[:] * (1-float(mix))
    else:
        newdata = AudioData(ndarray=dataB.data)
        newdata.data *= 1-float(mix)
        newdata.data[:dataA.endindex] += dataA.data[:] * float(mix)
    return newdata
    

class AudioFile(AudioData) :
    """
    The basic do-everything class for remixing. Acts as an `AudioData` 
    object, but with an added `analysis` selector which is an
    `AudioAnalysis` object.
    """
    def __init__(self, filename, verbose=True):
        """
        :param filename: path to a local MP3 file
        """
        if verbose:
            print >> sys.stderr, "Uploading file for analysis..."
        AudioData.__init__(self, filename=filename)
        self.analysis = AudioAnalysis(filename, PARSERS)
    


class ExistingTrack(object):
    """
    Analysis only (under the `analysis` selector), with a local file 
    known to be already analyzed by The `Echo Nest`_\'s servers.
    
    .. _Echo Nest: http://the.echonest.com/
    """
    def __init__(self, trackID_or_Filename, verbose=True):
        """
        :param trackID_or_Filename: a path to a local MP3 file or a
            valid Echo Nest `track identifier`_
        
        .. _track identifier: http://developer.echonest.com/docs/datatypes/?version=2#track_id
        """
        if(os.path.isfile(trackID_or_Filename)):
            trackID = hashlib.md5(file(trackID_or_Filename, 'rb').read()).hexdigest()
            if verbose:
                print >> sys.stderr, "Computed MD5 of file is " + trackID
        else:
            trackID = trackID_or_Filename
        self.analysis = AudioAnalysis(trackID, PARSERS)
    

class LocalAudioFile(AudioFile):
    """
    Like `AudioFile`, but with conditional upload: recommended. If a file 
    is already known to the Analyze API, then it does not bother uploading 
    the file.
    """
    def __init__(self, filename, verbose=True):
        """
        :param filename: path to a local MP3 file
        """
        trackID = hashlib.md5(file(filename, 'rb').read()).hexdigest()
        if verbose:
            print >> sys.stderr, "Computed MD5 of file is " + trackID
        try:
            if verbose:
                print >> sys.stderr, "Probing for existing analysis"
            tempanalysis = AudioAnalysis(trackID, {'duration': globalParserFloat})
            tempanalysis.duration
            self.analysis = AudioAnalysis(trackID, PARSERS)
            if verbose:
                print >> sys.stderr, "Analysis found. No upload needed."
        except util.EchoNestAPIThingIDError:
            if verbose:
                print >> sys.stderr, "Analysis not found. Uploading..."
            self.analysis = AudioAnalysis(filename, PARSERS)
        AudioData.__init__(self, filename=filename)
    

class LocalAnalysis(object):
    """
    Like `LocalAudioFile`, it conditionally uploads the file with which
    it was initialized. Unlike `LocalAudioFile`, it is not a subclass of 
    `AudioData`, so contains no sample data.
    """
    def __init__(self, filename, verbose=True):
        """
        :param filename: path to a local MP3 file
        """
        trackID = hashlib.md5(file(filename, 'rb').read()).hexdigest()
        if verbose:
            print >> sys.stderr, "Computed MD5 of file is " + trackID
        try:
            if verbose:
                print >> sys.stderr, "Probing for existing analysis"
            tempanalysis = AudioAnalysis(trackID, {'duration': globalParserFloat})
            tempanalysis.duration
            self.analysis = AudioAnalysis(trackID, PARSERS)
            if verbose:
                print >> sys.stderr, "Analysis found. No upload needed."
        except util.EchoNestAPIThingIDError:
            if verbose:
                print >> sys.stderr, "Analysis not found. Uploading..."
            self.analysis = AudioAnalysis(filename, PARSERS)
        # no AudioData.__init__()
    

class AudioQuantum(object) :
    """
    A unit of musical time, identified at minimum with a start time and 
    a duration, both in seconds. It most often corresponds with a `section`,
    `bar`, `beat`, `tatum`, or (by inheritance) `segment` obtained from an Analyze
    API call.
    
    Additional properties include:
    
    end
        computed time offset for convenience: `start` + `duration`
    container
        a circular reference to the containing `AudioQuantumList`,
        created upon creation of the `AudioQuantumList` that covers
        the whole track
    """
    def __init__(self, start=0, duration=0, kind=None, confidence=None) :
        """
        Initializes an `AudioQuantum`.
        
        :param start: offset from the start of the track, in seconds
        :param duration: length of the `AudioQuantum`
        :param kind: string containing what kind of rhythm unit it came from
        :param confidence: float between zero and one
        """
        self.start = start
        self.duration = duration
        self.kind = kind
        self.confidence = confidence
    
    def get_end(self):
        return self.start + self.duration
    
    end = property(get_end, doc="""
    A computed property: the sum of `start` and `duration`.
    """)
    
    def parent(self):
        """
        Returns the containing `AudioQuantum` in the rhythm hierarchy:
        a `tatum` returns a `beat`, a `beat` returns a `bar`, and a `bar` returns a
        `section`.
        """
        pars = {'tatum': 'beats',
                'beat':  'bars',
                'bar':   'sections'}
        try:
            uppers = getattr(self.container.container, pars[self.kind])
            return uppers.that(selection.overlap(self))[0]
        except LookupError:
            # Might not be in pars, might not have anything in parent.
            return None
    
    def children(self):
        """
        Returns an `AudioQuantumList` of the AudioQuanta that it contains,
        one step down the hierarchy. A `beat` returns `tatums`, a `bar` returns
        `beats`, and a `section` returns `bars`.
        """
        chils = {'beat':    'tatums',
                 'bar':     'beats',
                 'section': 'bars'}
        try:
            downers = getattr(self.container.container, chils[self.kind])
            return downers.that(selection.are_contained_by(self))
        except LookupError:
            return None
    
    def group(self):
        """
        Returns the `children`\() of the `AudioQuantum`\'s `parent`\(). 
        In other words: 'siblings'. If no parent is found, then return the
        `AudioQuantumList` for the whole track.
        """
        if self.parent():
            return self.parent().children()
        else:
            return self.container
    
    def prev(self, step=1):
        """
        Step backwards in the containing `AudioQuantumList`.
        Returns `self` if a boundary is reached.
        """
        group = self.container
        try:
            loc = group.index(self)
            new = max(loc - step, 0)
            return group[new]
        except:
            return self
    
    def next(self, step=1):
        """
        Step forward in the containing `AudioQuantumList`.
        Returns `self` if a boundary is reached.
        """
        group = self.container
        try:
            loc = group.index(self)
            new = min(loc + step, len(group))
            return group[new]
        except:
            return self
    
    def __str__(self):
        """
        Lists the `AudioQuantum`.kind with start and 
        end times, in seconds, e.g.::
        
            "segment (20.31 - 20.42)"
        """
        return "%s (%.2f - %.2f)" % (self.kind, self.start, self.end)
    
    def __repr__(self):
        """
        A string representing a constructor, including kind, start time, 
        duration, and (if it exists) confidence, e.g.::
        
            "AudioQuantum(kind='tatum', start=42.198267, duration=0.1523394)"
        """
        if self.confidence is not None:
            return "AudioQuantum(kind='%s', start=%f, duration=%f, confidence=%f)" % (self.kind, self.start, self.duration, self.confidence)
        else:
            return "AudioQuantum(kind='%s', start=%f, duration=%f)" % (self.kind, self.start, self.duration)
    
    def local_context(self):
        """
        Returns a tuple of (*index*, *length*) within rhythm siblings, where
        *index* is the (zero-indexed) position within its `group`\(), and 
        *length* is the number of siblings within its `group`\().
        """
        group = self.group()
        count = len(group)
        try:
            loc  = group.index(self)
        except: # seem to be some uncontained beats
            loc = 0
        return (loc, count,)
    
    def absolute_context(self):
        """
        Returns a tuple of (*index*, *length*) within the containing 
        `AudioQuantumList`, where *index* is the (zero-indexed) position within 
        its container, and *length* is the number of siblings within the
        container.
        """
        group = self.container
        count = len(group)
        loc = group.index(self)
        return (loc, count,)
    
    def context_string(self):
        """
        Returns a one-indexed, human-readable version of context.
        For example::
            
            "bar 4 of 142, beat 3 of 4, tatum 2 of 3"
        """
        if self.parent() and self.kind != "bar":
            return "%s, %s %i of %i" % (self.parent().context_string(),
                                  self.kind, self.local_context()[0] + 1,
                                  self.local_context()[1])
        else:
            return "%s %i of %i" % (self.kind, self.absolute_context()[0] + 1,
                                  self.absolute_context()[1])
    
    def __getstate__(self):
        """
        Eliminates the circular reference for pickling.
        """
        dictclone = self.__dict__.copy()
        del dictclone['container']
        return dictclone
    

class AudioSegment(AudioQuantum):
    """
    Subclass of `AudioQuantum` for the data-rich segments returned by
    the Analyze API. 
    """
    def __init__(self, start=0., duration=0., pitches=[], timbre=[], 
                 loudness_begin=0., loudness_max=0., time_loudness_max=0., loudness_end=None, kind='segment'):
        """
        Initializes an `AudioSegment`.
        
        :param start: offset from start of the track, in seconds
        :param duration: duration of the `AudioSegment`, in seconds
        :param pitches: a twelve-element list with relative loudnesses of each
                pitch class, from C (pitches[0]) to B (pitches[11])
        :param timbre: a twelve-element list with the loudness of each of a
                principal component of time and/or frequency profile
        :param kind: string identifying the kind of AudioQuantum: "segment"
        :param loudness_begin: loudness in dB at the start of the segment
        :param loudness_max: loudness in dB at the loudest moment of the 
                segment
        :param time_loudness_max: time (in sec from start of segment) of 
                loudest moment
        :param loudness_end: loudness at end of segment (if it is given)
        """
        self.start = start
        self.duration = duration
        self.pitches = pitches
        self.timbre = timbre
        self.loudness_begin = loudness_begin
        self.loudness_max = loudness_max
        self.time_loudness_max = time_loudness_max
        if loudness_end:
            self.loudness_end = loudness_end
        self.kind = kind
        self.confidence = None
    

class AudioQuantumList(list):
    """
    A container that enables content-based selection and filtering.
    A `List` that contains `AudioQuantum` objects, with additional methods
    for manipulating them.
    
    When an `AudioQuantumList` is created for a track via a call to the 
    Analyze API, `attach`\() is called so that its container is set to the
    containing `AudioAnalysis`, and the container of each of the 
    `AudioQuantum` list members is set to itself.
    
    Additional accessors now include AudioQuantum elements such as 
    `start`, `duration`, and `confidence`, which each return a List of the 
    corresponding properties in the contained AudioQuanta. A special name
    is `kinds`, which returns a List of the `kind` of each `AudioQuantum`.
    If `AudioQuantumList.kind` is "`segment`", then `pitches`, `timbre`,
    `loudness_begin`, `loudness_max`, `time_loudness_max`, and `loudness_end`
    are available.
    """
    QUANTUM_ATTRIBUTES = ['start', 'duration', 'confidence']
    SEGMENT_ATTRIBUTES = ['pitches', 'timbre', 'loudness_begin', 'loudness_max', 
                          'time_loudness_max', 'loudness_end']
    def __init__(self, kind = None, container = None):
        """
        Initializes an `AudioQuantumList`.
        
        :param kind: a label for the kind of `AudioQuantum` contained
            within
        :param container: a reference to the containing `AudioAnalysis`
        """
        list.__init__(self)
        self.kind = kind
        self.container = container
    
    def that(self, filt):
        """
        Method for applying a function to each of the contained
        `AudioQuantum` objects. Returns a new `AudioQuantumList` 
        of the same `kind` containing the `AudioQuantum` objects 
        for which the input function is true.
        
        See `echonest.selection` for example selection filters.
        
        :param filt: a function that takes one `AudioQuantum` and returns
            a `True` value `None`
            
        :change: experimenting with a filter-only form
        """
        out = AudioQuantumList(kind=self.kind)
        out.extend(filter(filt, self))
        return out
    
    def ordered_by(self, function, descending=False):
        """
        Returns a new `AudioQuantumList` of the same `kind` with the 
        original elements, but ordered from low to high according to 
        the input function acting as a key. 
        
        See `echonest.sorting` for example ordering functions.
        
        :param function: a function that takes one `AudioQuantum` and returns
            a comparison key
        :param descending: when `True`, reverses the sort order, from 
            high to low
        """
        out = AudioQuantumList(kind=self.kind)
        out.extend(sorted(self, key=function, reverse=descending))
        return out
    
    def beget(self, source, which=None):
        """
        There are two basic forms: a map-and-flatten and an converse-that.
        
        The basic form, with one `function` argument, returns a new 
        `AudioQuantumList` so that the source function returns
        `None`, one, or many AudioQuanta for each `AudioQuantum` contained within
        `self`, and flattens them, in order. ::
        
            beats.beget(the_next_ones)
        
        A second form has the first argument `source` as an `AudioQuantumList`, and
        a second argument, `which`, is used as a filter for the first argument, for
        *each* of `self`. The results are collapsed and accordianned into a flat
        list. 
        
        For example, calling::
        
            beats.beget(segments, which=overlap)
        
        Gets evaluated as::
        
            for beat in beats:
                return segments.that(overlap(beat))
        
        And all of the `AudioQuantumList`\s that return are flattened into 
        a single `AudioQuantumList`.
        
        :param source: A function of one argument that is applied to each
            `AudioQuantum` of `self`, or an `AudioQuantumList`, in which case
            the second argument is required.
        :param which: A function of one argument that acts as a `that`\() filter 
            on the first argument if it is an `AudioQuantumList`, or as a filter
            on the output, in the case of `source` being a function.
        """
        out = AudioQuantumList()
        if isinstance(source, AudioQuantumList):
            if not which:
                raise TypeError("'beget' requires a second argument, 'which'")
            out.extend(chain_from_mixed([source.that(which(x)) for x in self]))
        else:
            out.extend(chain_from_mixed(map(source, self)))
            if which:
                out = out.that(which)
        return out
    
    def attach(self, container):
        """
        Create circular references to the containing `AudioAnalysis` and for the 
        contained `AudioQuantum` objects.
        """
        self.container = container
        for i in self:
            i.container = self
    
    def __getstate__(self):
        """
        Eliminates the circular reference for pickling.
        """
        dictclone = self.__dict__.copy()
        del dictclone['container']
        return dictclone
    
    def __getattribute__(self, name):
        """
        In the case of `AudioQuantum` and `AudioSegment` accessors, return the 
        corresponding ones from each of the contained AudioQuanta. If the attribute
        is `kinds`, do the same for each `kind` accessor. Otherwise, do normal
        attribute dispatch.
        """
        if name in AudioQuantumList.SEGMENT_ATTRIBUTES and self.kind == 'segment':
            return [getattr(x, name) for x in self]
        elif name in AudioQuantumList.QUANTUM_ATTRIBUTES:
            return [getattr(x, name) for x in self]
        elif name == 'kinds':
            return [x.kind for x in self]
        else:
            return object.__getattribute__(self, name)
    

def dataParser(tag, doc):
    """
    Generic XML parser for `bars`, `beats`, and `tatums`.
    """
    out = AudioQuantumList(tag)
    nodes = doc.getElementsByTagName(tag)
    for n in nodes :
        out.append(AudioQuantum(start=float(n.firstChild.data), kind=tag,
                    confidence=float(n.getAttributeNode('confidence').value)))
    if len(out) > 1:
        for i in range(len(out) - 1) :
            out[i].duration = out[i+1].start - out[i].start
        out[-1].duration = out[-2].duration
    #else:
    #    out[0].duration = ???
    return out



def attributeParser(tag, doc) :
    """
    Generic XML parser for `sections` and (optionally) `segments`.
    """
    out = AudioQuantumList(tag)
    nodes = doc.getElementsByTagName(tag)
    for n in nodes :
        out.append( AudioQuantum(float(n.getAttribute('start')),
                                 float(n.getAttribute('duration')),
                                 tag) )
    return out


def globalParserFloat(doc) :
    """
    Generic XML parser for `tempo`, `duration`, `loudness`, `end_of_fade_in`,
    and `start_of_fade_out`.
    """
    d = doc.firstChild.childNodes[4].childNodes[0]
    if d.getAttributeNode('confidence'):
        return float(d.childNodes[0].data), float(d.getAttributeNode('confidence').value)
    else:
        return float(d.childNodes[0].data)



def globalParserInt(doc) :
    """
    Generic XML parser for `key`, `mode`, and `time_signature`.
    """
    d = doc.firstChild.childNodes[4].childNodes[0]
    if d.getAttributeNode('confidence'):
        return int(d.childNodes[0].data), float(d.getAttributeNode('confidence').value)
    else:
        return int(d.childNodes[0].data)



def barsParser(doc) :
    return dataParser('bar', doc)



def beatsParser(doc) :
    return dataParser('beat', doc)



def tatumsParser(doc) :
    return dataParser('tatum', doc)



def sectionsParser(doc) :
    return attributeParser('section', doc)



def segmentsParser(doc) :
    return attributeParser('segment', doc)



def metadataParser(doc) :
    """
    Creates a dictionary of metadata values from the Analyze API
    call.
    """
    out = {}
    for node in doc.firstChild.childNodes[4].childNodes:
        out[node.nodeName] = node.firstChild.data
    return out



def fullSegmentsParser(doc):
    """
    Full-featured parser for the XML returned by `get_segment` in the
    Analyze API.
    """
    out = AudioQuantumList('segment')
    nodes = doc.getElementsByTagName('segment')
    for n in nodes:
        start = float(n.getAttribute('start'))
        duration = float(n.getAttribute('duration'))
        
        loudnessnodes = n.getElementsByTagName('dB')
        loudness_end = None
        for l in loudnessnodes:
            if l.hasAttribute('type'):
                time_loudness_max = float(l.getAttribute('time'))
                loudness_max = float(l.firstChild.data)
            else:
                if float(l.getAttribute('time'))!=0:
                    loudness_end = float(l.firstChild.data)
                else:
                    loudness_begin = float(l.firstChild.data)
                    
        pitchnodes = n.getElementsByTagName('pitch')
        pitches=[]
        for p in pitchnodes:
            pitches.append(float(p.firstChild.data))
        
        timbrenodes = n.getElementsByTagName('coeff')
        timbre=[]
        for t in timbrenodes:
            timbre.append(float(t.firstChild.data))
        
        out.append(AudioSegment(start=start, duration=duration, pitches=pitches, 
                        timbre=timbre, loudness_begin=loudness_begin, 
                        loudness_max=loudness_max, time_loudness_max=time_loudness_max, loudness_end=loudness_end ))
    return out


def chain_from_mixed(iterables):
    """
    Helper function to flatten a list of elements and lists
    into a list of elements.
    """
    for y in iterables: 
        try:
            iter(y)
            for element in y:
                yield element
        except:
            yield y

PARSERS =  { 'bars' : barsParser, 
             'beats' : beatsParser,
             'sections' : sectionsParser,
             'segments' : fullSegmentsParser,
             'tatums' : tatumsParser,
             'metadata' : metadataParser,
             'tempo' : globalParserFloat,
             'duration' : globalParserFloat,
             'loudness' : globalParserFloat,
             'end_of_fade_in' : globalParserFloat,
             'start_of_fade_out' : globalParserFloat,
             'key' : globalParserInt,
             'mode' : globalParserInt,
             'time_signature' : globalParserInt,
             }
"""
A shorthand input for `AudioAnalysis`, associating keys (which are also
exposed as accessors via `AudioAnalysis.__getattribute__`\()) with 
parsing functions.
"""

class FileTypeError(Exception):
    def __init__(self, filename, message):
        self.filename = filename
        self.message = message
        
    def __str__(self):
        return self.message+': '+self.filename
