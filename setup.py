from setuptools import setup, find_packages

with open("qth/version.py", "r") as f:
    exec(f.read())

setup(
    name="qth",
    version=__version__,
    packages=find_packages(),

    # Metadata for PyPi
    url="https://github.com/mossblaser/qth",
    author="Jonathan Heathcote",
    description="Qb-Than's Home-automation messaging library.",
    license="GPLv2",
    classifiers=[
        "Development Status :: 3 - Alpha",

        "Intended Audience :: Developers",

        "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",

        "Operating System :: POSIX :: Linux",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: MacOS",

        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
    ],
    keywords="mqtt asyncio home-automation messaging",

    # Requirements
    install_requires=["aiomqtt>=0.1.0", "sentinel>=0.1.1"],
)
