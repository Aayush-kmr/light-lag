"""Physical constants. Sources noted inline; all are standard published values."""

C_KM_S = 299_792.458                 # speed of light, exact (SI)
AU_KM = 149_597_870.7                # IAU 2012 Resolution B2, exact
DAY_S = 86_400.0

# Gravitational parameters (km^3/s^2), DE440 values as distributed in NAIF gm_de440.tpc
MU_SUN = 1.3271244004127942e11
MU_EARTH = 398_600.435507
MU_MARS = 42_828.375816

R_EARTH_KM = 6378.1366              # equatorial radius, pck00011
R_MARS_KM = 3396.19                  # equatorial radius, pck00011

# Mean obliquity of the ecliptic at J2000 (IAU 1976), used for the analytic fallback
OBLIQUITY_J2000_DEG = 23.43928

G0 = 9.80665                         # standard gravity, m/s^2

# Sun sidereal rotation (Carrington, ~25.38 d) for the Parker spiral model
SUN_OMEGA_RAD_S = 2.0 * 3.141592653589793 / (25.38 * DAY_S)

# One-way light time for 1 AU (for explanations): ~499.005 s
LT_1AU_S = AU_KM / C_KM_S
