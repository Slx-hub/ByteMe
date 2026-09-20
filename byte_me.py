from PIL import Image
import os
import sys
import argparse

########################
## GLOBALS
########################

palette_file = 'C:/Users/Janis/Documents/ePaper_palette.txt'
palette_colors = []

input_path = 'input/'
output_path = 'output/'

base_path = os.path.dirname(__file__)

########################
## FUNCTIONS
########################

def load_palette(file):
    with open(file, 'r') as palette:
        global palette_colors
        palette_colors = palette.read().splitlines()
        print('palette loaded with these colors: ', palette_colors)

def get_files(path):
    return [os.path.join(path, f) for f in os.listdir(path)]

def write_pic_to_file(file_path):
    image = Image.open(file_path).convert('RGB')
    with open(to_output_path(file_path), 'wb') as output:
        write_bytes(image.size, image.load(), output)


def write_bytes(img_size, img_pixels, output):
    buffer = bytearray()

    for y in range(img_size[1]):
        for x in range(0, img_size[0], 2):
            try:
                byte = bytes.fromhex('{}{}'.format(get_color_index(img_pixels[x, y]), get_color_index(img_pixels[x + 1, y])))
                buffer.extend(byte)
            except Exception as e:
                raise Exception('Exception on pixel <{}, {}> with color {}'.format(x, y, img_pixels[x, y]), e)
    output.write(buffer)

def get_color_index(color):
    return str(palette_colors.index(to_hex_string(color)))

########################
## HELPERS
########################

def full_path(path):
    return os.path.join(base_path, path)

def to_output_path(path):
    return os.path.splitext(path.replace('input', 'output'))[0] + '.glds'

def to_hex_string(color):
    return 'FF' + '%0.2X%0.2X%0.2X' % (color[0], color[1], color[2])

########################
## MAIN
########################
print('Let\'s take some bytes out of these images\n')

# Parse command line arguments
parser = argparse.ArgumentParser(description='Convert images to custom byte format')
parser.add_argument('-i', '--input', type=str, help='Specific input image file (relative to input_path)')
args = parser.parse_args()

load_palette(full_path(palette_file))

if args.input:
    # Convert only the specified file
    images = [full_path(os.path.join(input_path, args.input))]
    print(f'Converting specific file: {args.input}\n')
else:
    # Convert all files in input_path
    images = get_files(full_path(input_path))
    print('Found these image files: ', images, '\n')

for image in images:
    print('eating', image)
    write_pic_to_file(image)

#bytes(newFileBytes)