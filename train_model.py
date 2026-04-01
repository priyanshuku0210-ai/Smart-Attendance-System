import cv2
import os
import numpy as np
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(BASE_DIR, "dataset")
TRAINER_DIR = os.path.join(BASE_DIR, "trainer")
TRAINER_FILE = os.path.join(TRAINER_DIR, "trainer.yml")

training_in_progress = False
training_status_message = "Idle"
training_progress = 0


def set_training_status(message, progress=None):
    global training_status_message, training_progress
    training_status_message = message
    if progress is not None:
        training_progress = progress


def get_training_status():
    return {
        "training": training_in_progress,
        "message": training_status_message,
        "progress": training_progress
    }


def train_face_model(status_callback=None):
    global training_in_progress, training_status_message, training_progress

    def update_status(message, progress=None):
        set_training_status(message, progress)
        if status_callback:
            status_callback(message, progress)

    try:
        training_in_progress = True
        training_progress = 0
        update_status("Initializing training...", 0)

        os.makedirs(DATASET_DIR, exist_ok=True)
        os.makedirs(TRAINER_DIR, exist_ok=True)

        if not hasattr(cv2, "face"):
            training_in_progress = False
            update_status("OpenCV face module not found.", 0)
            return False, "OpenCV face module not found. Install opencv-contrib-python."

        recognizer = cv2.face.LBPHFaceRecognizer_create()

        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        detector = cv2.CascadeClassifier(cascade_path)

        if detector.empty():
            training_in_progress = False
            update_status("Could not load Haar Cascade file.", 0)
            return False, "Could not load Haar Cascade file."

        faces = []
        ids = []

        folder_names = [
            folder_name for folder_name in os.listdir(DATASET_DIR)
            if os.path.isdir(os.path.join(DATASET_DIR, folder_name))
        ]

        if len(folder_names) == 0:
            training_in_progress = False
            update_status("No student folders found.", 0)
            return False, "No student folders found in dataset."

        valid_folders = []
        total_images = 0

        update_status("Scanning dataset folders...", 5)

        for folder_name in folder_names:
            folder_path = os.path.join(DATASET_DIR, folder_name)
            try:
                int(folder_name)
                valid_folders.append(folder_name)
                total_images += len([
                    f for f in os.listdir(folder_path)
                    if os.path.isfile(os.path.join(folder_path, f))
                ])
            except ValueError:
                continue

        if total_images == 0:
            training_in_progress = False
            update_status("No images found for training.", 0)
            return False, "No face images found. Capture faces first."

        processed_images = 0

        for folder_index, folder_name in enumerate(valid_folders, start=1):
            folder_path = os.path.join(DATASET_DIR, folder_name)

            try:
                student_id = int(folder_name)
            except ValueError:
                continue

            update_status(
                f"Processing student ID {student_id} ({folder_index}/{len(valid_folders)})...",
                min(10 + int((folder_index / len(valid_folders)) * 20), 30)
            )

            for image_name in os.listdir(folder_path):
                image_path = os.path.join(folder_path, image_name)

                if not os.path.isfile(image_path):
                    continue

                img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
                processed_images += 1

                if img is None:
                    continue

                detected_faces = detector.detectMultiScale(
                    img,
                    scaleFactor=1.3,
                    minNeighbors=5
                )

                if len(detected_faces) == 0:
                    faces.append(img)
                    ids.append(student_id)
                else:
                    for (x, y, w, h) in detected_faces:
                        face_crop = img[y:y + h, x:x + w]
                        if face_crop.size > 0:
                            faces.append(face_crop)
                            ids.append(student_id)

                progress = 30 + int((processed_images / total_images) * 50)
                update_status(
                    f"Reading training images... {processed_images}/{total_images}",
                    min(progress, 80)
                )

        if len(faces) == 0:
            training_in_progress = False
            update_status("No valid face data found.", 0)
            return False, "No valid face images found. Capture proper face images first."

        update_status("Training model, please wait...", 85)
        time.sleep(0.3)

        recognizer.train(faces, np.array(ids))

        update_status("Saving trained model...", 95)
        recognizer.save(TRAINER_FILE)

        training_in_progress = False
        update_status("Model trained successfully.", 100)
        return True, "Model trained successfully."

    except Exception as e:
        training_in_progress = False
        training_progress = 0
        training_status_message = f"Training failed: {str(e)}"
        return False, f"Training failed: {str(e)}"