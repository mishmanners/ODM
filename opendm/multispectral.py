from opendm import dls
import math
# Loosely based on https://github.com/micasense/imageprocessing/blob/master/micasense/utils.py

def dn_to_radiance(photo, image):
    """
    Convert Digital Number values to Radiance values
    :param photo ODM_Photo
    :param image numpy array containing image data
    :return numpy array with radiance image values
    """
    # Handle thermal bands (experimental)
    if photo.band_name == 'LWIR':
        image -= (273.15 * 100.0) # Convert Kelvin to Celsius
        image = image.astype(float) * 0.01
        return image
    
    # All others
    a1, a2, a3 = photo.get_radiometric_calibration()
    dark_level = photo.get_dark_level()

    exposure_time = photo.exposure_time
    gain = photo.get_gain()

    V, x, y = vignette_map(photo)
    if x is None:
        x, y = np.meshgrid(np.arange(photo.width), np.arange(photo.height))

    if dark_level is not None:
        image -= dark_level

    if V is not None:
        # vignette correction
        image *= V

    if exposure_time and a2 is not None and a3 is not None:
        # row gradient correction
        R = 1.0 / (1.0 + a2 * y / exposure_time - a3 * y)
        image *= R
    
    # Floor any negative radiances to zero (can happend due to noise around blackLevel)
    if dark_level is not None:
        image[image < 0] = 0
    
    # apply the radiometric calibration - i.e. scale by the gain-exposure product and
    # multiply with the radiometric calibration coefficient
    # need to normalize by 2^16 for 16 bit images
    # because coefficients are scaled to work with input values of max 1.0

    bps = photo.bits_per_sample
    if bps:
        bit_depth_max = float(2 ** bps)
    else:
        # Infer from array dtype
        info = np.iinfo(image.dtype)
        bit_depth_max = info.max - info.min
    
    image = image.astype(float)

    if gain is not None and exposure_time is not None:
        image /= (gain * exposure_time)
    
    if a1 is not None:
        image *= a1
    
    image /= bit_depth_max

    return image

def vignette_map(photo):
    x_vc, y_vc = photo.get_vignetting_center()
    polynomial = photo.get_vignetting_polynomial()

    if x_vc and polynomial:
        # reverse list and append 1., so that we can call with numpy polyval
        polynomial.reverse()
        polynomial.append(1.0)
        vignette_poly = np.array(polynomial)

        # perform vignette correction
        # get coordinate grid across image
        x, y = np.meshgrid(np.arange(photo.width), np.arange(photo.height))

        # meshgrid returns transposed arrays
        x = x.T
        y = y.T

        # compute matrix of distances from image center
        r = np.hypot((x - x_vc), (y - y_vc))

        # compute the vignette polynomial for each distance - we divide by the polynomial so that the
        # corrected image is image_corrected = image_original * vignetteCorrection

        vignette = 1.0 / np.polyval(vignette_poly, r)
        return vignette, x, y
    
    return None, None, None

def dn_to_reflectance(photo, image, use_sun_sensor=True):
    radiance = dn_to_radiance(photo, image)
    irradiance_scale = compute_irradiance_scale_factor(photo, use_sun_sensor=use_sun_sensor)
    return radiance * irradiance_scale

def compute_irradiance_scale_factor(photo, use_sun_sensor=True):
    # Thermal?
    if photo.band_name == "LWIR":
        return 1.0

    if photo.irradiance is not None:
        horizontal_irradiance = photo.irradiance
        return math.pi / horizontal_irradiance
    
    if use_sun_sensor:
        # Estimate it
        dls_orientation_vector = np.array([0,0,-1])
        sun_vector_ned, sensor_vector_ned, sun_sensor_angle, \
        solar_elevation, solar_azimuth = dls.compute_sun_angle([photo.latitude, photo.longitude],
                                        (0,0,0), # TODO: add support for sun sensor pose
                                        photo.utc_time,
                                        dls_orientation_vector)

        angular_correction = dls.fresnel(sun_sensor_angle)

        # TODO: support for direct and scattered irradiance

        direct_to_diffuse_ratio = 6.0 # Assumption
        spectral_irradiance = photo.sun_sensor # TODO: support for XMP:SpectralIrradiance

        percent_diffuse = 1.0 / direct_to_diffuse_ratio
        sensor_irradiance = spectral_irradiance / angular_correction

        # find direct irradiance in the plane normal to the sun
        untilted_direct_irr = sensor_irradiance / (percent_diffuse + np.cos(sun_sensor_angle))
        direct_irradiance = untilted_direct_irr
        scattered_irradiance = untilted_direct_irr*percent_diffuse

        # compute irradiance on the ground using the solar altitude angle
        horizontal_irradiance = direct_irradiance * np.sin(solar_elevation) + scattered_irradiance
        return math.pi / horizontal_irradiance
    
    return 1.0